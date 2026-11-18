from tools.live_evaluation_data import COHORTS, phygen_score


def test_phygen_scores_do_not_cross_text_vision_boundary():
    summary = {'models': {'m': {'cases': 160, 'mean_0_1': 0.5, 'mean_1_5': 3.0}}}
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
