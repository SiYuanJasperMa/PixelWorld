"""PixelWorld-Flash harness variant: precomputed input metadata and output helpers.

This module changes the tool interface only. It does not generate, complete or
modify any pixel; every RGB frame is still written by the agent's generate.py.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .util import atomic_json, sha256

FLASH_SCHEMA = "pixelworld-flash-v1"
ASSET_HELPERS = Path(__file__).resolve().parent / "assets" / "flash"
HELPERS = ("pixelworld_check.py", "pixelworld_run.py")

RULES_ADDENDUM = (
    "\n- Harness helpers: `INPUT_SUMMARY.json` gives the canvas, prompt and first-frame "
    "statistics. `python pixelworld_run.py` runs `generate.py` and validates every required "
    "output in one step; `python pixelworld_check.py` re-validates without regenerating. "
    "`artifact_io.save(frames, case_dir)` takes a uint8 array shaped "
    "`(frames, height, width, 3)` and writes `pixels.npz`, `frames/`, `inspection.png`, "
    "`transition_check.png` and `index.html`.\n"
    "- Use `.tmp/` for temporary files. `/tmp` is outside the workspace and is blocked by "
    "the sandbox.\n"
)

RULES_ADDENDUM_LEAN = (
    "\n- Harness helpers: `python pixelworld_run.py` runs `generate.py` and validates every "
    "required output in one step; `python pixelworld_check.py` re-validates without "
    "regenerating. `artifact_io.save(frames, case_dir)` takes a uint8 array shaped "
    "`(frames, height, width, 3)` and writes `pixels.npz`, `frames/`, `inspection.png`, "
    "`transition_check.png` and `index.html`.\n"
    "- Use `.tmp/` for temporary files. `/tmp` is outside the workspace and is blocked by "
    "the sandbox.\n"
)


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _first_frame_summary(path: Path, limit: int = 256) -> dict:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = image.size
    scale = min(1.0, limit / max(width, height))
    small = image.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                         Image.Resampling.BILINEAR)
    rgb = np.asarray(small, dtype=np.float64)
    luma = rgb @ np.array([0.299, 0.587, 0.114])
    gy, gx = np.gradient(luma)
    quantized = (np.asarray(small, dtype=np.uint16) >> 3).astype(np.int32)
    codes = (quantized[:, :, 0] << 10) | (quantized[:, :, 1] << 5) | quantized[:, :, 2]
    values, counts = np.unique(codes.reshape(-1), return_counts=True)
    order = np.argsort(counts)[-8:][::-1]
    total = int(counts.sum())
    dominant = []
    for index in order:
        code = int(values[index])
        dominant.append({
            "rgb": [(code >> 10) << 3, ((code >> 5) & 31) << 3, (code & 31) << 3],
            "share": round(float(counts[index]) / total, 4),
        })
    rows = []
    for row in np.array_split(np.arange(rgb.shape[0]), 3):
        cells = []
        for column in np.array_split(np.arange(rgb.shape[1]), 3):
            cells.append([round(float(value), 1) for value in rgb[np.ix_(row, column)].mean(axis=(0, 1))])
        rows.append(cells)
    return {
        "file": path.name,
        "width": width,
        "height": height,
        "mean_rgb": [round(float(value), 1) for value in rgb.mean(axis=(0, 1))],
        "std_rgb": [round(float(value), 1) for value in rgb.std(axis=(0, 1))],
        "luma_mean": round(float(luma.mean()), 1),
        "luma_std": round(float(luma.std()), 1),
        "grid_3x3_mean_rgb": rows,
        "dominant_colors": dominant,
        "edge_density": round(float(np.mean(np.hypot(gx, gy) > 12)), 4),
    }


def input_summary(spec: dict, source: Path) -> dict:
    summary = {
        "schema": FLASH_SCHEMA,
        "task_type": spec["task_type"],
        "prompt": (source / "prompt.txt").read_text().strip(),
        "canvas": {
            "width": spec["width"],
            "height": spec["height"],
            "frames": spec["frames"],
            "fps": spec["fps"],
            "duration_seconds": spec["frames"] / spec["fps"],
        },
        "required_outputs": ["generate.py", "pixels.npz", "inspection.png",
                             "transition_check.png", "index.html"],
        "artifact_io": ("artifact_io.save(frames, case_dir) expects a uint8 array shaped "
                        "(frames, height, width, 3) and writes pixels.npz, frames/, "
                        "inspection.png, transition_check.png and index.html."),
        "python_packages": {name: _available(name) for name in ("numpy", "PIL", "cv2", "scipy")},
        "helpers": {
            "pixelworld_run.py": "python pixelworld_run.py - run generate.py, then validate outputs",
            "pixelworld_check.py": "python pixelworld_check.py - validate outputs without regenerating",
        },
    }
    frame = source / "first_frame.png"
    if spec["task_type"] in ("i2v", "v2v", "video_edit") and frame.is_file():
        summary["first_frame"] = _first_frame_summary(frame)
    return summary


def install(workspace: Path, spec: dict, source: Path, include_summary: bool = True) -> dict:
    """Install the Flash interface into an already-prepared case workspace."""
    helpers = {}
    for name in HELPERS:
        origin = ASSET_HELPERS / name
        target = workspace / name
        shutil.copy2(origin, target)
        helpers[name] = {"sha256": sha256(target)}
    summary_record = None
    if include_summary:
        summary_path = workspace / "INPUT_SUMMARY.json"
        atomic_json(summary_path, input_summary(spec, source))
        summary_record = {"file": summary_path.name, "sha256": sha256(summary_path)}
    rules = workspace / "GENERATION_RULES.md"
    text = rules.read_text()
    if "Harness helpers:" not in text:
        addendum = RULES_ADDENDUM if include_summary else RULES_ADDENDUM_LEAN
        rules.write_text(text.rstrip("\n") + "\n" + addendum)
    return {
        "schema": FLASH_SCHEMA,
        "input_summary": summary_record,
        "helpers": helpers,
        "rules_sha256": sha256(rules),
    }


def install_api(workspace: Path, receipt: bool = False) -> dict:
    """Expose a compact save API while retaining the exact original output writer.

    The original implementation remains inspectable inside the case workspace.
    No input summary, image synthesis, execution helper or refinement cap is added.
    """
    backend = workspace / '_pixelworld_io'
    backend.mkdir()
    (backend / '__init__.py').write_text('')
    original = workspace / 'artifact_io.py'
    original_hash = sha256(original)
    shutil.copy2(original, backend / 'backend.py')
    template = workspace / 'player_template.html'
    if template.is_file():
        shutil.copy2(template, backend / template.name)
        template.unlink()
    interface = ASSET_HELPERS / ('artifact_io_receipt.py' if receipt else 'artifact_io_api.py')
    shutil.copy2(interface, original)
    return {'schema': 'pixelworld-save-api-v1', 'receipt': receipt,
            'original_writer_sha256': original_hash,
            'backend_sha256': sha256(backend / 'backend.py'),
            'interface_sha256': sha256(original),
            'rules_sha256': sha256(workspace / 'GENERATION_RULES.md')}


def pack_task(text: str, workspace: Path):
    """Include exact allowed static task text up front, without generating a summary."""
    records={};sections=[text.rstrip()]
    for name in ('prompt.txt','GENERATION_RULES.md','task_spec.json'):
        path=workspace/name
        if not path.is_file():continue
        records[name]=sha256(path)
        sections.append('Exact workspace file '+name+':\n````\n'+path.read_text().rstrip()+'\n````')
    return '\n\n'.join(sections)+'\n',{'schema':'pixelworld-taskpack-v1','source_sha256':records,
        'semantic_summary_added':False,'files_unchanged':True}
