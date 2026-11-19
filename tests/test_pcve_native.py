import json
from pathlib import Path

import pytest

from tools.pcve_native import native_directory,native_cache,native_setup,native_summary,SAM2_KERNEL_SHA256
from pixelworld.util import sha256


def ready_bundle(tmp_path):
    folder=tmp_path/'state/sam2_native_eval';folder.mkdir(parents=True)
    extension=folder/'build/_C.so';extension.parent.mkdir();extension.write_bytes(b'fixture')
    record={'extension':str(extension),'extension_sha256':sha256(extension),
            'smoke_pass':True,'random_mask_parity_tests':32,'host_compiler_gate_override':False,
            'cuda_architectures':'8.0;9.0+PTX','source_sha256':SAM2_KERNEL_SHA256}
    (folder/'ready.json').write_text(json.dumps(record))
    return extension


def test_native_ready_rejects_modified_binary(tmp_path):
    extension=ready_bundle(tmp_path)
    assert native_directory(tmp_path)==extension.parent
    extension.write_bytes(b'changed-after-parity-test')
    with pytest.raises(ValueError):native_directory(tmp_path)


def test_cache_cannot_reuse_non_native_or_different_extension(tmp_path):
    extension=ready_bundle(tmp_path)
    original=native_cache(tmp_path,extension.parent)
    assert original.is_relative_to(tmp_path/'state/pcve_native_gt_tracks')
    extension.write_bytes(b'another-version')
    assert native_cache(tmp_path,extension.parent)!=original
    assert native_summary(tmp_path/'evaluation')!=tmp_path/'evaluation/summary.json'


def test_native_ready_rejects_hopper_only_binary(tmp_path):
    ready_bundle(tmp_path)
    record=tmp_path/'state/sam2_native_eval/ready.json';ready=json.loads(record.read_text())
    ready['cuda_architectures']='9.0';record.write_text(json.dumps(ready))
    with pytest.raises(ValueError):native_directory(tmp_path)


def test_driver_explicitly_loads_verified_extension_and_fresh_cache(tmp_path):
    extension=ready_bundle(tmp_path);cache=native_cache(tmp_path,extension.parent)
    setup=native_setup(extension.parent,cache)
    assert 'from sam2 import _C' in setup
    assert 'assert _C.__file__' in setup
    assert 'get_connected_componnets(_native_mask)' in setup
    assert 'warnings.filterwarnings' in setup
    assert str(cache) in setup
    assert '__defaults__[:-1]' in setup
