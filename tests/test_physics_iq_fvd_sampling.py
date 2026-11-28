import shutil

import numpy as np
from PIL import Image
import pytest

from tools.evaluate_physics_iq_fvd import sample


@pytest.mark.parametrize('height,width,frames', [(12, 28, 24), (28, 12, 48)])
def test_packed_rgb_and_png_sources_produce_identical_fvd_inputs(tmp_path, height, width, frames):
    workspace = tmp_path / 'workspace'
    folder = workspace / 'frames'
    folder.mkdir(parents=True)
    rng = np.random.default_rng(41)
    rgb = rng.integers(0, 256, (frames, height, width, 3), dtype=np.uint8)
    for index, image in enumerate(rgb):
        Image.fromarray(image).save(folder / f'{index:02d}.png')
    values = rgb.astype(np.uint32)
    packed = (values[..., 0] << 16) | (values[..., 1] << 8) | values[..., 2]
    np.savez_compressed(workspace / 'pixels.npz', pixels=packed.transpose(1, 2, 0))
    expected = sample(tmp_path, frames, 24)
    shutil.rmtree(folder)
    actual = sample(tmp_path, frames, 24)
    assert actual.shape == (16, 224, 224, 3)
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize('tensor', [np.zeros((12, 28, 23), dtype=np.uint32),
                                  np.zeros((12, 28, 24), dtype=np.float32)])
def test_tensor_fallback_rejects_an_incorrect_frame_grid_or_dtype(tmp_path, tensor):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    np.savez_compressed(workspace / 'pixels.npz', pixels=tensor)
    with pytest.raises(ValueError, match='frame grid'):
        sample(tmp_path, 24, 24)
