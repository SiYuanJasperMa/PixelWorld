import json

from pixelworld import pcve
from tools.baseline_data import video_baselines
from tools.watch_minimax_pcve import evaluation_scope


def test_full_scope_preserves_calibration_definition(tmp_path):
    subset = tmp_path/'subset.json'
    subset.write_text(json.dumps({'case_count':10,'indices':list(range(10))}))
    assert evaluation_scope(tmp_path)==(subset,10,'evaluation_subset')
    assert evaluation_scope(tmp_path,True)==(None,129,'evaluation')
    assert json.loads(subset.read_text())['case_count']==10


def test_full_baseline_requires_complete_native_full129(tmp_path):
    root=tmp_path/'baselines/minimax_h3_local'
    (root/'evaluation_subset').mkdir(parents=True)
    (root/'evaluation').mkdir()
    (root/'progress.json').write_text(json.dumps({'total':129,'completed':10}))
    (root/'evaluation_subset/summary.json').write_text(json.dumps({
        'case_count':10,'scores':{'overall':{'pes':-.2666}}}))
    def local():
        return next(r for r in video_baselines(tmp_path)if r['key']=='minimax-h3-local')
    assert local()['label']=='MiniMax H3 · 本地全量'
    assert not local()['scores']
    summary={'model':'minimax-h3-local','track':'video-edit','case_count':129,
        'complete_official129':True,'source_revision':pcve.EVALUATOR_REVISION,
        'dataset_revision':pcve.DATASET_REVISION,'sam2_native_postprocessing':False,
        'scores':{'overall':{'pes':.2,'traj_px':10,'mask_iou':.3,'psnr':30,
            'ssim':.9,'lpips':.1,'clip_sim':.8},'fvd':100}}
    f=root/'evaluation/summary.json';f.write_text(json.dumps(summary))
    assert not local()['scores']
    summary['sam2_native_postprocessing']=True;f.write_text(json.dumps(summary))
    assert local()['scores']['pcve']['cases']==129
    assert local()['scores']['pcve']['value']==.2
