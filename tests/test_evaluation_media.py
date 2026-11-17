import pytest

from pixelworld.evaluation_media import frame_mapping


def test_lower_fps_is_displayed_with_explicit_frame_hold():
    indices = frame_mapping(60, 12, 24)
    assert len(indices) == 120
    assert indices == [i//2 for i in range(120)]
    assert len(set(indices)) == 60


def test_higher_fps_uses_existing_time_aligned_frames():
    assert frame_mapping(240, 48, 24) == list(range(0, 240, 2))
    assert frame_mapping(120, 24, 24) == list(range(120))


def test_inexact_duration_is_not_silently_trimmed():
    with pytest.raises(ValueError, match='exactly'):
        frame_mapping(61, 12, 25)
