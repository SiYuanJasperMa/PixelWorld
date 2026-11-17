"""Evaluator-only time-grid normalization, without learned/interpolated frames."""
from __future__ import annotations
import math


def frame_mapping(source_frames: int, source_fps: float, target_fps: float = 24) -> list[int]:
    if source_frames < 1 or source_fps <= 0 or target_fps <= 0:
        raise ValueError('Frame count and rates must be positive')
    duration = source_frames / source_fps
    target_frames = round(duration * target_fps)
    if not math.isclose(target_frames / target_fps, duration, abs_tol=1e-9):
        raise ValueError('Duration cannot be represented exactly on the evaluation grid')
    # A video displays each sample until its next sample. Floor sampling gives
    # that same zero-order hold: lower rates repeat existing samples, higher
    # rates select existing samples. No synthetic intermediate RGB is created.
    return [min(source_frames-1, math.floor(i*source_fps/target_fps + 1e-9))
            for i in range(target_frames)]
