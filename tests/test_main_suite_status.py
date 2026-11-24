import json

import pytest

from tools.build_main_suite_status import (
    JUDGE, appearance_scores, harness_progress, legacy_score, phygen_score, public_counts, rbench_score,
)


def test_appearance_requires_both_orders_on_the_full_benchmark():
    from tools.evaluate_video_photographic_gpt61 import RUBRIC_SHA256, VERSION

    report = {'phase':'finished','judge':JUDGE,'version':VERSION,'rubric_sha256':RUBRIC_SHA256,
              'completed_calls':976,'expected_calls':976,'failures':[], 'cohorts':[
        {'benchmark':'rbench_common_manipulation','model':'gpt61-sol','cases':1,'ratings':2,'no_evidence':0,'mean':4.0},
        {'benchmark':'physics_iq_720p24fps','model':'gpt61-sol','cases':198,'ratings':396,'no_evidence':0,'mean':4.42},
        {'benchmark':'phygenbench_t2v_main720p24fps','model':'deepseek-v41','cases':160,'ratings':319,'no_evidence':1,'mean':2.18},
    ]}
    scores = appearance_scores(report)
    assert scores['gpt61-sol']['rbench']['score'] is None
    assert scores['gpt61-sol']['physics_iq']['score'] == 4.42
    assert scores['deepseek-v41']['phygenbench']['score'] is None
    report['phase'] = 'running'
    assert appearance_scores(report) == {}


def test_rbench_never_displays_a_partial_or_failed_cohort_mean():
    row = {'scored': 49, 'expected': 50, 'full_common50': True, 'mean': 0.71}
    report = {'protocol': 'rbench-common-official-gpt-branch-gpt61-sol-v1',
              'judge': JUDGE, 'models': {'example': row}}
    assert rbench_score(report, 'example') == (49, None)
    row.update(scored=50, judge_errors=['0049'])
    assert rbench_score(report, 'example') == (50, None)
    row['judge_errors'] = []
    assert rbench_score(report, 'example') == (50, 0.71)
    report['judge'] = 'legacy'
    assert rbench_score(report, 'example') == (0, None)


def test_phygen_requires_all_160_cases_with_the_new_judge():
    row = {'cases': 159, 'mean': 0.73}
    report = {'version': 'phygen-gpt61-sol-three-stage-mcq-v1', 'judge': JUDGE,
              'full160': True, 'cases': list(range(1, 161)), 'models': {'example': row}}
    assert phygen_score(report, 'example') == (159, None)
    row['cases'] = 160
    assert phygen_score(report, 'example') == (160, 0.73)
    report['cases'][2] = 2
    assert phygen_score(report, 'example') == (0, None)


def test_pes_requires_full129_and_keeps_negative_or_zero_values():
    state = {'full_scope_scored': True, 'score': {'scope': 99, 'metrics': {'PES': -0.1}}}
    assert legacy_score(state, 129, 'PES') is None
    state['score']['scope'] = 129
    assert legacy_score(state, 129, 'PES') == -0.1
    state['score']['metrics']['PES'] = 0.
    assert legacy_score(state, 129, 'PES') == 0.
    with pytest.raises(ValueError):
        public_counts({'counts': {'complete': 129, 'pending': 1}}, 129)


def test_harness_progress_exposes_only_registered_counts(tmp_path):
    model = 'gpt62-sol'
    config, runs = tmp_path/'opencode.json', tmp_path/'runs'
    runs.mkdir()
    config.write_text(json.dumps({'runs_root':str(runs)}))
    registry = {'models':{model:{'opencode':{'config':str(config),'registered_cases':198,
                'full_supervisor_started':True}}}}
    (tmp_path/'dispatch.json').write_text(json.dumps(registry))
    (runs/'status.json').write_text(json.dumps({'cases':198,'groups':{f'{model}/vision-agent':{
        'model_key':model,'counts':{'complete':2,'running':1,'pending':195},'private_location':'do not copy'}}}))
    result = harness_progress(tmp_path)[model]['opencode']
    assert result['state'] == 'full_released' and result['counts']['complete'] == 2
    assert set(result) == {'state','counts','expected','full_released'}
    registry['models'][model]['opencode']['config'] = str(tmp_path.parent/'outside.json')
    (tmp_path/'dispatch.json').write_text(json.dumps(registry))
    with pytest.raises(ValueError,match='Unregistered native matrix configuration'):
        harness_progress(tmp_path)


def test_dashboard_polls_the_published_snapshot_instead_of_blind_reloads():
    from tools.build_main_suite_status import render

    html = render({'protocol': 'pixelworld-main-suite-20261124',
                   'updated_utc': '2026-11-24T00:00:00+00:00', 'models': {}, 'references': {},
                   'photographic_progress': {}, 'photographic_scores': {},
                   'photographic_judge': JUDGE, 'harness_progress': {}})
    assert 'name="pixelworld-updated" content="2026-11-24T00:00:00+00:00"' in html
    assert 'checkSnapshot' in html
    assert 'http-equiv="refresh"' not in html
