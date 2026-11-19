"""Verified SAM2 postprocessing and isolated evaluation provenance."""
from pathlib import Path
import json

from pixelworld import pcve
from pixelworld.util import sha256


NATIVE_EVALUATION = 'sam2_native_v2'
SAM2_KERNEL_SHA256 = '926c277e24f40609622bb4ce49912838d1c799e63edcccc1c75e65e701acd497'


def native_directory(bundle):
    record = Path(bundle) / 'state/sam2_native_eval/ready.json'
    ready = json.loads(record.read_text())
    native = Path(ready['extension']).parent
    if (ready.get('smoke_pass') is not True
            or ready.get('random_mask_parity_tests', 0) < 32
            or ready.get('host_compiler_gate_override') is not False
            or ready.get('cuda_architectures') != '8.0;9.0+PTX'
            or ready.get('source_sha256') != SAM2_KERNEL_SHA256
            or not ready.get('extension_sha256')
            or sha256(native / '_C.so') != ready['extension_sha256']):
        raise ValueError('Verified SAM2 extension and matching parity-test hash required')
    return native


def native_setup(native, cache):
    native = Path(native).resolve()
    if not (native / '_C.so').is_file():
        raise FileNotFoundError(native / '_C.so')
    return (f'import sam2\nsam2.__path__.insert(0,{str(native)!r})\nfrom sam2 import _C\n'
            + f'assert _C.__file__ == {str(native / "_C.so")!r}, "Unexpected SAM2 native extension"\n'
            + "import torch,warnings\n_native_mask=torch.zeros((1,1,8,8),dtype=torch.uint8,device='cuda')\n"
            + '_native_mask[:,:,1:3,1:3]=1\n'
            + '_native_labels,_native_counts=_C.get_connected_componnets(_native_mask)\n'
            + 'assert torch.all(_native_counts[_native_mask.bool()]==4), "SAM2 native kernel failed on this GPU"\n'
            + 'warnings.filterwarnings("error",message="(?s).*Skipping the post-processing.*",category=UserWarning)\n'
            + "print('SAM2 native postprocessing enabled:', _C.__file__, flush=True)\n"
            + 'from metrics import physics\n'
            + 'physics.PhysicsScorer.__init__.__defaults__='
            + f'physics.PhysicsScorer.__init__.__defaults__[:-1]+({str(Path(cache).resolve())!r},)\n')


def native_cache(bundle, native):
    # Cache identity changes with the dataset and compiled implementation.
    return (Path(bundle) / 'state/pcve_native_gt_tracks' / pcve.DATASET_REVISION
            / sha256(Path(native) / '_C.so'))


def native_summary(cohort):
    return Path(cohort) / NATIVE_EVALUATION / 'summary.json'
