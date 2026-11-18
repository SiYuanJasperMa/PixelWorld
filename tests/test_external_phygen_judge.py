import base64
import io

import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("openai")
import numpy as np
from PIL import Image
from tools.evaluate_phygen_gpt55 import video_images


@pytest.mark.parametrize("total", [120, 121])
def test_external_video_uses_agent_five_second_window(tmp_path, total):
    path = tmp_path / "baseline.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (16, 16))
    if not writer.isOpened():
        pytest.skip("MP4 encoder unavailable")
    for index in range(total):
        value = 255 if index == 120 else index
        writer.write(np.full((16, 16, 3), value, dtype=np.uint8))
    writer.release()
    images, indices = video_images(path, 8, 768)
    assert indices == [0, 17, 34, 51, 68, 85, 102, 119]
    assert len(images) == 8
    final = np.array(Image.open(io.BytesIO(base64.b64decode(images[-1]))))
    assert abs(float(final.mean()) - 119) < 8


def test_external_video_rejects_unmatched_fps(tmp_path):
    path = tmp_path / "slow.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, (16, 16))
    if not writer.isOpened():
        pytest.skip("MP4 encoder unavailable")
    for _ in range(120):
        writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    with pytest.raises(ValueError, match="Expected 24 FPS"):
        video_images(path, 8, 768)
