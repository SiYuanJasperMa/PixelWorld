import pytest

from pixelworld.physics_iq import evaluation_frame_indices


def test_lower_fps_grid_repeats_existing_frames_without_interpolation():
    indices = evaluation_frame_indices({'fps': 12, 'frames': 60}, 24)
    assert indices == [index // 2 for index in range(120)]
    assert len(indices) == 120 and indices[-1] == 59


def test_higher_fps_grid_selects_existing_frames():
    assert evaluation_frame_indices({'fps': 48, 'frames': 240}, 24) == list(range(0, 240, 2))


def test_native_export_and_main_grid_preserve_frame_sequence():
    assert evaluation_frame_indices({'fps': 4, 'frames': 20}) == list(range(20))
    assert evaluation_frame_indices({'fps': 24, 'frames': 120}, 24) == list(range(120))


def test_grid_rejects_unapproved_rates_or_duration():
    with pytest.raises(ValueError):
        evaluation_frame_indices({'fps': 48, 'frames': 239}, 24)
    with pytest.raises(ValueError):
        evaluation_frame_indices({'fps': 30, 'frames': 150}, 24)
