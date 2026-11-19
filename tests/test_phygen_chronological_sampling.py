import base64
import io
import shutil
import numpy as np
from PIL import Image
import pytest

from tools.evaluate_phygen_gpt55 import frame_images,FRAME_SAMPLING_REVISION
from tools.live_evaluation_data import phygen_score


def frames(run):
    folder=run/'workspace/frames';folder.mkdir(parents=True)
    for i in range(120):Image.new('RGB',(8,8),(i,i,i)).save(folder/f'{i:02d}.png')
    (run/'workspace/generate.py').write_text('pass\n')


def test_judge_samples_real_frame_numbers_past_99(tmp_path):
    run=tmp_path/'run';frames(run)
    encoded,indices=frame_images(run,8,768)
    assert indices==[0,17,34,51,68,85,102,119]
    observed=[np.asarray(Image.open(io.BytesIO(base64.b64decode(item)))).mean()for item in encoded]
    assert np.allclose(observed,indices,atol=3)


def test_legacy_sampling_summary_is_not_a_current_main_score():
    summary={'track':'vision-agent','models':{'m':{'cases':160,'mean_0_1':0.5,'mean_1_5':3}}}
    assert phygen_score(summary,'m','vision-agent') is None
    summary['frame_sampling_revision']=FRAME_SAMPLING_REVISION
    assert phygen_score(summary,'m','vision-agent') is not None


def test_smoke_preview_preserves_numeric_frame_order(tmp_path,monkeypatch):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    cv2=pytest.importorskip('cv2')
    run=tmp_path/'run';frames(run)
    from tools import prepare_hf_delivery
    monkeypatch.setattr(prepare_hf_delivery,'export_trace_run',lambda run,path:path.write_bytes(b'fixture'))
    from tools.watch_phygen_acd_smoke import exported_run
    preview=exported_run(run,tmp_path/'output',ffmpeg)
    assert preview.name=='video_chrono_v2.mp4'
    capture=cv2.VideoCapture(str(preview));observed=[]
    while True:
        ok,frame=capture.read()
        if not ok:break
        observed.append(frame.mean())
    capture.release()
    assert len(observed)==120
    assert np.allclose(np.array(observed)[[0,17,34,51,68,85,102,119]],[0,17,34,51,68,85,102,119],atol=4)
