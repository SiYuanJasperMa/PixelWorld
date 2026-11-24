import json

from tools.evaluate_phygen_three_stage_gpt55 import model_source
from tools.generate_minimax_phygen import captions
from tools.baseline_data import video_baselines
from tools.phygen_protocol import THREE_STAGE_REVISION


def test_minimax_source_is_t2v_video_not_pcve_or_legacy_judgment(tmp_path):
    assert model_source(tmp_path,'minimax-h3-local',7)==tmp_path/'baselines/minimax_h3_phygen/videos/phygen_007.mp4'
    legacy=tmp_path/'evaluation/phygenbench_gpt55_chronological_v2/m'
    legacy.mkdir(parents=True)
    (legacy/'007.json').write_text(json.dumps({'run':'existing-run'}))
    assert str(model_source(tmp_path,'m',7))=='existing-run'


def test_captions_require_same_official_question_order(tmp_path):
    root=tmp_path/'vendor/phygenbench-official-source/PhyGenBench'
    root.mkdir(parents=True)
    rows=[{'caption':f'prompt {i}'}for i in range(160)]
    (root/'prompts.json').write_text(json.dumps(rows))
    (root/'video_question.json').write_text(json.dumps(rows))
    assert captions(tmp_path)==rows
    rows.reverse()
    (root/'video_question.json').write_text(json.dumps(rows))
    import pytest
    with pytest.raises(ValueError):captions(tmp_path)


def test_phygen_progress_and_score_do_not_mix_with_pcve_or_partial_results(tmp_path):
    root=tmp_path/'baselines/minimax_h3_phygen';root.mkdir(parents=True)
    (root/'progress.json').write_text(json.dumps({'completed':1,'total':160,'phase':'generation_running'}))
    output=tmp_path/'evaluation/minimax_h3_phygen_gpt55_three_stage_mcq_v1'
    output.mkdir(parents=True)
    def baseline():
        return next(r for r in video_baselines(tmp_path)if r['key']=='minimax-h3-local')
    assert baseline()['benchmark_progress']['phygenbench']['completed']==1
    summary={'version':THREE_STAGE_REVISION,'full160':False,'failures':[],
        'models':{'minimax-h3-local':{'cases':1,'mean':.7}}}
    (output/'summary.json').write_text(json.dumps(summary))
    assert not baseline()['scores']
    summary.update(full160=True);summary['models']['minimax-h3-local']['cases']=160
    (output/'summary.json').write_text(json.dumps(summary))
    assert baseline()['scores']['phygenbench']['value']==.7
    assert 'pcve'not in baseline()['scores']


def test_parallel_progress_counts_committed_outputs_instead_of_primary_visit_count(tmp_path):
    root=tmp_path/'baselines/minimax_h3_phygen';(root/'backfill').mkdir(parents=True)
    (root/'progress.json').write_text(json.dumps({'completed':1,'total':160,'phase':'generation_running'}))
    for case in [1,150]:
        directory=root/'cases'/f'phygen_{case:03d}';directory.mkdir(parents=True)
        (directory/'generation.json').write_text(json.dumps({'status':'completed'}))
    (root/'gpu_state.json').write_text(json.dumps({'phase':'running','gpus':list(range(8))}))
    (root/'backfill/gpu_state.json').write_text(json.dumps({'phase':'running','gpus':list(range(8))}))
    item=next(r for r in video_baselines(tmp_path)if r['key']=='minimax-h3-local')
    assert item['benchmark_progress']['phygenbench']['completed']==2
    assert item['benchmark_progress']['phygenbench']['allocated_gpus']==16
