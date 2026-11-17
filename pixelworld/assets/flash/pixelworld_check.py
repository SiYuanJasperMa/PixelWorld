#!/usr/bin/env python3
"""Validate PixelWorld outputs without regenerating or modifying them.

Prints one compact JSON line. Exit code 0 means every contract check passed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "task_spec.json"
SPEC = json.loads(SPEC_PATH.read_text()) if SPEC_PATH.is_file() else {}
WIDTH = int(SPEC.get("width", 256))
HEIGHT = int(SPEC.get("height", 144))
FRAMES = int(SPEC.get("frames", 20))
REQUIRED = ["generate.py", "pixels.npz", "inspection.png", "transition_check.png", "index.html"]
if SPEC.get("audio"):
    REQUIRED.append("audio.wav")


def _first_frame() -> np.ndarray:
    image = Image.open(ROOT / "first_frame.png").convert("RGB")
    if SPEC.get("conditioning_resize_filter") == "lanczos":
        image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    return np.asarray(image)


def check() -> dict:
    problems = []
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        problems.append("missing: " + ", ".join(missing))
    frame_paths = sorted((ROOT / "frames").glob("*.png")) if (ROOT / "frames").is_dir() else []
    if len(frame_paths) != FRAMES:
        problems.append(f"frames: {len(frame_paths)}/{FRAMES}")
    shape = None
    frame0_exact = None
    if (ROOT / "pixels.npz").is_file():
        try:
            pixels = np.load(ROOT / "pixels.npz")["pixels"]
            shape = [int(value) for value in pixels.shape]
            if shape != [HEIGHT, WIDTH, FRAMES] or pixels.dtype != np.uint32:
                problems.append(
                    f"pixels: shape {shape} dtype {pixels.dtype}, expected {[HEIGHT, WIDTH, FRAMES]} uint32")
            else:
                mismatched = None
                for index in range(FRAMES):
                    path = ROOT / "frames" / f"{index:02d}.png"
                    if not path.is_file():
                        mismatched = f"frames/{index:02d}.png missing"
                        break
                    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint32)
                    packed = (rgb[:, :, 0] << 16) | (rgb[:, :, 1] << 8) | rgb[:, :, 2]
                    if rgb.shape != (HEIGHT, WIDTH, 3) or not np.array_equal(packed, pixels[:, :, index]):
                        mismatched = f"frame {index:02d} differs from pixels.npz"
                        break
                if mismatched:
                    problems.append(mismatched)
                if SPEC.get("task_type") == "i2v" and (ROOT / "first_frame.png").is_file():
                    frame0_exact = bool(np.array_equal(_first_frame(), np.asarray(
                        Image.open(ROOT / "frames" / "00.png").convert("RGB"))))
                    if not frame0_exact:
                        problems.append("frame 00 is not pixel-identical to first_frame.png")
        except Exception as error:  # noqa: BLE001 - report any unreadable artifact
            problems.append(f"pixels.npz unreadable: {type(error).__name__}: {error}")
    for name in ("inspection.png", "transition_check.png", "index.html"):
        path = ROOT / name
        if path.is_file() and path.stat().st_size == 0:
            problems.append(f"{name} is empty")
    return {
        "pass": not problems,
        "frames": len(frame_paths),
        "pixels_shape": shape,
        "frame0_exact": frame0_exact,
        "problems": problems,
        "next": ("view inspection.png" if not problems
                 else "fix the problems above, then rerun python pixelworld_run.py"),
    }


def main() -> int:
    report = check()
    print(json.dumps(report, separators=(",", ":")))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
