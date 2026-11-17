import pytest
from tools.evaluate_flash_subset import one_match


def test_generated_mask_matching_requires_case_identifier(tmp_path):
    (tmp_path / '0001_video-masks_4FPS_left_take-1_ball.mp4').touch()
    correct = tmp_path / '0002_video-masks_4FPS_left_take-1_cube.mp4'
    correct.touch()
    assert one_match(tmp_path, '0002_video-masks_4FPS_left_*.mp4') == correct
    with pytest.raises(ValueError):
        one_match(tmp_path, '*.mp4')
    with pytest.raises(ValueError):
        one_match(tmp_path, '0003_*.mp4')
