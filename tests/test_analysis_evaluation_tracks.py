from tools.live_evaluation_data import COHORTS, phygen_score


def test_phygen_scores_do_not_cross_text_vision_boundary():
    summary = {'frame_sampling_revision': 'chronological-rgb-v2', 'models': {'m': {'cases': 160, 'mean_0_1': 0.5, 'mean_1_5': 3.0}}}
    assert phygen_score(summary, 'm', 'vision-agent')
    assert phygen_score(summary, 'm', 'text') is None
    summary['track'] = 'text'
    assert phygen_score(summary, 'm', 'text')
    assert phygen_score(summary, 'm', 'vision-agent') is None


def test_all_registered_analysis_conditions_have_separate_score_locations():
    conditions = ['pcve_text', 'robojudge_text', 'phygenbench_text', 'physics_iq_text',
                  'physics_iq_res480', 'physics_iq_res1080', 'physics_iq_fps12', 'physics_iq_fps48',
                  'physics_iq_thinking_low', 'physics_iq_thinking_higher']
    assert all(key in COHORTS for key in conditions)
    assert len({COHORTS[key][1] for key in conditions}) == len(conditions)


def test_model_keyed_phygen_scheduler_does_not_block_all_evaluation_refreshes(tmp_path):
    import json
    from tools.live_evaluation_data import collect
    runs=tmp_path/'analysis_generations/phygenbench_text';runs.mkdir(parents=True)
    (runs/'status.json').write_text(json.dumps({'groups':{'m/text':{'counts':{'pending':160}}}}))
    (tmp_path/'live_sources.json').write_text(json.dumps({'sources':[{'key':'phygenbench_text','runs_root':str(runs)}]}))
    target=tmp_path/'evaluation/phygenbench_text_gpt55_chronological_v2';target.mkdir(parents=True)
    (target/'scheduler.json').write_text(json.dumps({'track':'text','cohorts':{'m':{'phase':'waiting_for_generation'}}}))
    rows=collect(tmp_path)['cohorts']
    assert len(rows)==1 and rows[0]['model_key']=='m' and rows[0]['track']=='text'
    assert rows[0]['phase']=='waiting_for_generation'
