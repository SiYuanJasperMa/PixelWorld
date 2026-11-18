import pytest
import sys
import json
import hashlib
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


def test_subset_update_uses_retry_artifacts_and_isolates_prior_exports(tmp_path, monkeypatch):
    from pathlib import Path
    import tools.evaluate_flash_subset as evaluator
    cohort=tmp_path/'cohort';cohort.mkdir()
    (cohort/'experiment_manifest.json').write_text(json.dumps({'arms':['candidate'],'case_ids':['0001']}))
    for attempt,success in [(1,False),(2,True)]:
        run=cohort/f'generations/candidate/model/vision-agent/case_0001_attempt_{attempt:02d}'
        run.mkdir(parents=True)
        (run/'run_manifest.json').write_text(json.dumps({'case_id':'0001','attempt':attempt,
            'status':'complete' if success else 'failed','pixels_sha256':'retry-pixels' if success else 'old-pixels',
            'validation':{'pass':success,**({} if success else {'infra_error':'storage'})}}))
        (run/'media_contract.json').write_text(json.dumps({'fps':24,'frames':120}))
    resource=tmp_path/'resource';(resource/'protocol').mkdir(parents=True)
    description=resource/'protocol/descriptions_base.csv'
    description.write_text('generated_video_name,scenario\n0001_perspective-left_scene.mp4,_take-1_scene\n')
    monkeypatch.setattr(evaluator,'PHYSICS_IQ_DESCRIPTIONS_SHA256',hashlib.sha256(description.read_bytes()).hexdigest())
    monkeypatch.setattr(evaluator.subprocess,'check_output',
                        lambda args,**kwargs:evaluator.PHYSICS_IQ_COMMIT if args[-1]=='HEAD' else '')
    monkeypatch.setattr(evaluator,'check_pool_memory',lambda *args:{})
    old=cohort/'quality/native_videos/candidate/0001_perspective-left_scene.mp4'
    old.parent.mkdir(parents=True);old.write_bytes(b'prior incomplete export')
    exports=[]
    monkeypatch.setattr(evaluator,'export',lambda run,native,sampled,ffmpeg,spec:exports.append((run,native)))
    jobs=[]
    def score(job):
        jobs.append(job)
        return {'arm':job['arm'],'case':job['case'],'attempt':job['attempt'],
                'v1':.1,'v2':.2,'pixels_sha256':job['pixels_sha256']}
    monkeypatch.setattr(evaluator,'score',score)
    class Pool:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def map(self,fn,values):return map(fn,values)
    monkeypatch.setattr(evaluator,'ProcessPoolExecutor',Pool)
    assert evaluator.update(cohort,resource,'ffmpeg','model',1,native_scoring=True)==1
    assert exports[0][0].name=='case_0001_attempt_02'
    assert 'attempt_02' in exports[0][1].parts
    assert 'attempt_02' in Path(jobs[0]['mask_dir']).parts
    assert jobs[0]['pixels_sha256']=='retry-pixels' and jobs[0]['attempt']==2
    assert old.read_bytes()==b'prior incomplete export'
