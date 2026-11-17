"""Output-only API: save(frames, case_dir='.', audio=None).

frames: uint8 RGB, shape (T,H,W,3), matching task_spec.json.
Writes pixels.npz, frames/*.png, inspection.png, transition_check.png, index.html.
Audio, when required, is int16 (samples,channels). No pixels are generated here.
The inspectable storage implementation and player are in _pixelworld_io/.
"""
from _pixelworld_io.backend import save as _save


def save(frames, case_dir='.', audio=None):
    return _save(frames, case_dir, audio=audio)
