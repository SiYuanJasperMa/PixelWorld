import pytest
import sys
from types import ModuleType
from tools.evaluate_flash_subset import one_match, score


def test_generated_mask_matching_requires_case_identifier(tmp_path):
    (tmp_path / '0001_video-masks_4FPS_left_take-1_ball.mp4').touch()
    correct = tmp_path / '0002_video-masks_4FPS_left_take-1_cube.mp4'
    correct.touch()
    assert one_match(tmp_path, '0002_video-masks_4FPS_left_*.mp4') == correct
    with pytest.raises(ValueError):
        one_match(tmp_path, '*.mp4')
    with pytest.raises(ValueError):
        one_match(tmp_path, '0003_*.mp4')


@pytest.mark.parametrize('fps,frames',[(4,20),(24,120)])
def test_subset_scoring_uses_matching_reference_fps_and_all_selected_frames(tmp_path,monkeypatch,fps,frames):
    package=ModuleType('physiq');package.__path__=[]
    mask_module=ModuleType('physiq.binary_mask_generator')
    metrics_module=ModuleType('physiq.calculate_and_write_metrics_to_csv')
    def generate_mask(video,output,is_real):
        from pathlib import Path
        (Path(output).parent/f'0001_video-masks_{fps}FPS_perspective-left_take-1_scene.mp4').touch()
    calls=[]
    def process_view(paths,view,start,end,consider):
        calls.append((paths,start,end,consider))
        return {f'weighted_spatial_iou_v1_{view}':.1,f'weighted_spatial_iou_v2_{view}':.2}
    mask_module.generate_mask=generate_mask
    metrics_module.ViewPaths=lambda *paths:paths
    metrics_module.process_view=process_view
    monkeypatch.setitem(sys.modules,'physiq',package)
    monkeypatch.setitem(sys.modules,'physiq.binary_mask_generator',mask_module)
    monkeypatch.setitem(sys.modules,'physiq.calculate_and_write_metrics_to_csv',metrics_module)
    real=tmp_path/f'dataset/split-videos/testing/{fps}FPS';real.mkdir(parents=True)
    for id,take in [('0001','take-1'),('0199','take-2')]:
        (real/f'{id}_testing-videos_{fps}FPS_perspective-left_{take}_scene.mp4').touch()
    job={'arm':'baseline','case':'0001','upstream':str(tmp_path),'dataset':str(tmp_path/'dataset'),
         'video':str(tmp_path/'0001_perspective-left_scene.mp4'),'mask_dir':str(tmp_path/'generated-masks'),
         'pixels_sha256':'unchanged','fps':fps,'frames':frames}
    result=score(job)
    assert calls[0][1:]==(0,frames,frames)
    assert f'/{fps}FPS/' in calls[0][0][0]
    assert result['scoring_fps']==fps and result['scoring_frames']==frames
