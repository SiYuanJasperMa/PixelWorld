import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from pixelworld.config import load_config
from pixelworld.flash import install

ASSETS = Path(__file__).resolve().parents[1] / "pixelworld" / "assets"


def _case(tmp_path: Path, frames: int = 4) -> Path:
    source = tmp_path / "case"
    source.mkdir()
    spec = {"task_type": "i2v", "width": 32, "height": 24, "frames": frames, "fps": 4, "audio": None}
    (source / "task_spec.json").write_text(json.dumps(spec))
    (source / "prompt.txt").write_text("A red ball rolls to the right.\n")
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:, :, 0] = 180
    Image.fromarray(image).save(source / "first_frame.png")
    return source


def _workspace(tmp_path: Path, source: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("prompt.txt", "task_spec.json", "first_frame.png"):
        shutil.copy2(source / name, workspace / name)
    shutil.copy2(ASSETS / "GENERATION_RULES.md", workspace / "GENERATION_RULES.md")
    shutil.copy2(ASSETS / "artifact_io.py", workspace / "artifact_io.py")
    shutil.copy2(ASSETS / "player_template.html", workspace / "player_template.html")
    return workspace


def test_flash_variant_parsing(tmp_path):
    value = {
        "inputs_root": "inputs", "runs_root": "runs", "harness_variant": "flash-v1",
        "models": [{"key": "m", "model": "actual", "tracks": ["vision-agent"]}],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    assert load_config(path).harness_variant == "flash-v1"


def test_install_writes_summary_and_helpers(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    record = install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    summary = json.loads((workspace / "INPUT_SUMMARY.json").read_text())
    assert summary["canvas"] == {"width": 32, "height": 24, "frames": 4, "fps": 4,
                                 "duration_seconds": 1.0}
    assert summary["prompt"].startswith("A red ball")
    assert summary["first_frame"]["width"] == 32
    assert len(summary["first_frame"]["grid_3x3_mean_rgb"]) == 3
    assert "pixels" not in summary
    assert (workspace / "pixelworld_run.py").is_file()
    assert (workspace / "pixelworld_check.py").is_file()
    assert "Harness helpers:" in (workspace / "GENERATION_RULES.md").read_text()
    assert record["input_summary"]["sha256"]


def test_rules_addendum_is_idempotent(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    spec = json.loads((source / "task_spec.json").read_text())
    install(workspace, spec, source)
    install(workspace, spec, source)
    assert (workspace / "GENERATION_RULES.md").read_text().count("Harness helpers:") == 1


def _run_check(workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "pixelworld_check.py"], cwd=workspace,
                          capture_output=True, text=True, timeout=120)


def test_check_reports_missing_outputs(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    result = _run_check(workspace)
    assert result.returncode == 1
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert not report["pass"]
    assert any("missing" in problem for problem in report["problems"])


def test_check_accepts_valid_outputs(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    sys.path.insert(0, str(workspace))
    try:
        import artifact_io
        frames = np.zeros((4, 24, 32, 3), dtype=np.uint8)
        frames[:, :, :, 0] = 180
        frames[1:, 10:14, 8:12, 1] = 255
        (workspace / "generate.py").write_text("raise SystemExit('fixture')\n")
        artifact_io.save(frames, workspace)
    finally:
        sys.path.remove(str(workspace))
    result = _run_check(workspace)
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert result.returncode == 0, report
    assert report["pass"] and report["frame0_exact"]
