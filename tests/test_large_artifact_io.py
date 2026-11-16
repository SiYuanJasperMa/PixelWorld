import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest
from PIL import Image


def test_large_helper_retains_exact_rgb_and_avoids_inline_video_payload(tmp_path):
    path=Path(__file__).parents[1]/'pixelworld/assets/artifact_io_large.py'
    spec=importlib.util.spec_from_file_location('large_helper',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'task_spec.json').write_text(json.dumps({'frames':5,'height':3,'width':4,'fps':24,'audio':None}))
    rgb=np.random.default_rng(3).integers(0,256,(5,3,4,3),dtype=np.uint8)
    # A list exercises the bounded path: the helper must not stack all source
    # frames into another full in-memory video before packing.
    module.save(list(rgb),tmp_path)
    with np.load(tmp_path/'pixels.npz')as data:packed=data['pixels']
    decoded=np.stack([(packed>>s)&255 for s in (16,8,0)],axis=-1).transpose(2,0,1,3).astype(np.uint8)
    np.testing.assert_array_equal(decoded,rgb)
    for i in range(5):np.testing.assert_array_equal(np.asarray(Image.open(tmp_path/'frames'/f'{i:02d}.png')),rgb[i])
    html=(tmp_path/'index.html').read_text();assert 'base64' not in html and 'image.decode' in html
    assert not (tmp_path/'pixels.packing.npy').exists()
    assert not (tmp_path/'pixels.npz.partial').exists()


def test_large_helper_cleans_partial_tensor_after_invalid_frame(tmp_path):
    path=Path(__file__).parents[1]/'pixelworld/assets/artifact_io_large.py'
    spec=importlib.util.spec_from_file_location('large_helper_failure',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'task_spec.json').write_text(json.dumps({'frames':2,'height':3,'width':4,'fps':24,'audio':None}))
    frames=[np.zeros((3,4,3),dtype=np.uint8),np.zeros((2,4,3),dtype=np.uint8)]
    with pytest.raises(AssertionError):module.save(frames,tmp_path)
    assert not (tmp_path/'pixels.packing.npy').exists()
    assert not (tmp_path/'pixels.npz.partial').exists()
