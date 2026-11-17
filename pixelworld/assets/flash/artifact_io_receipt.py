"""Output-only API: save(frames, case_dir='.', audio=None).

frames: uint8 RGB, shape (T,H,W,3), matching task_spec.json.
Writes pixels.npz, frames/*.png, inspection.png, transition_check.png, index.html.
Audio, when required, is int16 (samples,channels). No pixels are generated here.
Prints a compact write receipt; it does not certify final harness acceptance.
The inspectable storage implementation and player are in _pixelworld_io/.
"""
import json
from pathlib import Path
from _pixelworld_io.backend import save as _save


def save(frames, case_dir='.', audio=None):
    _save(frames, case_dir, audio=audio)
    spec = json.loads((Path(case_dir) / 'task_spec.json').read_text())
    receipt = {'written': 'pixels.npz', 'frames': spec['frames'],
               'rgb': [spec['height'], spec['width'], 3], 'fps': spec['fps'],
               'inspection': 'inspection.png', 'preview': 'index.html'}
    print(json.dumps(receipt, separators=(',', ':')))
    return receipt
