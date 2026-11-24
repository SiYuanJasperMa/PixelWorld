import json

import pytest

from tools.build_main_suite_status import (
    BENCHMARKS, JUDGE, appearance_scores, harness_progress, legacy_score, native_main_smoke, native_smoke_weekly_deltas,
    phygen_score, public_counts, rbench_score, render,
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


def dashboard_status(models=None, **extra):
    return {'protocol': 'pixelworld-main-suite-20261124', 'updated_utc': '2026-11-24T00:00:00+00:00',
            'models': models or {}, 'references': {}, 'photographic_progress': {}, **extra}


def test_dashboard_restores_benchmark_details_and_keeps_trace_viewers():
    cells = {key: {'counts': {'complete': 1, 'running': 1, 'pending': total-2, 'exhausted': 0},
                   'expected': total, 'score': None, 'scored': 0, 'metric': metric}
             for key, (_, total, metric) in BENCHMARKS.items()}
    html = render(dashboard_status({'gpt61-sol': cells, 'gpt62-sol': cells}))
    for page in ('physics_iq_baseline.html', 'phygen_pixelspace.html', 'pcve_rigid.html'):
        assert f'<a href="{page}">' in html
        assert f'href="{page}?model=gpt61-sol"' in html
        assert f'href="{page}?model=gpt62-sol"' not in html
    assert 'href="videos.html?benchmark=rbench"' in html
    assert 'videos.html?index=physics_iq_720p24fps_gpt62%2Fgpt62-sol%2Fvision-agent%2Findex.json' in html
    assert 'videos.html?index=rbench_common_manipulation_gpt61-sol%2Fgpt61-sol%2Fvision-agent%2Findex.json' in html
    assert html.count('视频与 Trace →') == 8


def test_native_smoke_exports_only_actual_consumption_and_safe_metadata(tmp_path):
    sidecar = tmp_path/'smoke.json'
    sidecar.write_text(json.dumps({'model': 'gpt-6.2-astra', 'cli_version': 'codex-cli 0.172.0',
        'reasoning_effort': 'high', 'service_tier': 'default', 'status': 'running',
        'balance': 'PRIVATE_BALANCE', 'account': {'email': 'PRIVATE_EMAIL'}, 'samples': [
            {'benchmark': 'physics_iq', 'case': '0001', 'state': 'complete', 'duration_seconds': 123.4,
             'token_usage': {'input_tokens': 12345, 'cached_input_tokens': 10000, 'output_tokens': 678,
                             'balance': 'PRIVATE_NESTED_BALANCE'},
             'credits_consumed': 0, 'quota_weekly_consumed': 1, 'credits_estimated': 2.5,
             'usd_estimated': None, 'auth': 'PRIVATE_AUTH'},
            {'benchmark': 'rbench', 'case': '0001', 'state': 'generating', 'token_usage': None},
            {'benchmark': 'private', 'case': 'PRIVATE_CASE'}]}))
    smoke = native_main_smoke(sidecar)
    encoded = json.dumps(smoke)
    assert 'PRIVATE' not in encoded and 'balance' not in encoded and 'account' not in encoded
    assert len(smoke['samples']) == 4
    first, second = smoke['samples'][:2]
    assert first['token_usage'] == {'input_tokens': 12345, 'cached_input_tokens': 10000, 'output_tokens': 678}
    assert first['credits_consumed'] == 0 and first['credits_estimated'] == 2.5
    assert first['quota_weekly_consumed'] == 1
    assert second['token_usage'] is None and second['credits_consumed'] is None
    html = render(dashboard_status(native_main_smoke=smoke))
    assert 'GPT-6.2-Astra · 主实验配置 smoke' in html
    assert '0.172.0' in html and '0.169.0' in html
    assert 'Input tokens' in html and '12,345' in html and '10,000' in html and '678' in html
    assert '123.4 秒' in html and '<td>0.0000</td>' in html and '<td>2.5000</td>' in html
    assert '周额度 · 实测消耗（百分点）' in html and '<td>1.00</td>' in html
    assert 'Credits · 余额差' in html and 'Credits · 估算' in html and 'USD · 估算' in html
    assert '生成中' in html and '待记录' in html and 'PRIVATE' not in html
    assert html.split('<section id="native-main-smoke">')[1].split('</section>')[0].count('<tr>') == 5


def test_native_weekly_delta_requires_same_window_and_private_telemetry_paths(tmp_path):
    root = tmp_path/'run'
    telemetry = root/'telemetry'
    telemetry.mkdir(parents=True)
    source = tmp_path/'source.json'
    source.write_text(json.dumps({'root': str(root)}))
    before, after = telemetry/'before.json', telemetry/'after.json'
    def receipt(path, percent, reset=123):
        path.write_text(json.dumps({'rate_limits': {'rateLimits': {
            'primary': {'usedPercent': percent, 'windowDurationMins': 10080, 'resetsAt': reset},
            'credits': {'balance': 'PRIVATE_BALANCE'}}, 'accountId': 'PRIVATE_ACCOUNT'}}))
    receipt(before, 69)
    receipt(after, 70)
    sample = {'benchmark': 'physics_iq', 'case': '0001', 'quota_before': str(before), 'quota_after': str(after)}
    progress = root/'progress.json'
    progress.write_text(json.dumps({'samples': [sample]}))
    assert native_smoke_weekly_deltas(source) == {(1, 'physics_iq', '0001'): 1}
    receipt(after, 1, reset=456)
    assert native_smoke_weekly_deltas(source) == {}
    outside = tmp_path/'outside.json'
    receipt(outside, 70)
    link = telemetry/'outside-link.json'
    link.symlink_to(outside)
    sample['quota_after'] = str(link)
    progress.write_text(json.dumps({'samples': [sample]}))
    assert native_smoke_weekly_deltas(source) == {}


def test_native_smoke_preserves_first_round_and_appends_four_fresh_samples(tmp_path):
    sidecar = tmp_path/'smoke.json'
    samples = [{'round': round_no, 'benchmark': key, 'case': f'000{round_no}',
                'state': 'complete' if round_no == 1 else 'pending',
                'token_usage': {'input_tokens': 100, 'output_tokens': 20} if round_no == 1 else None}
               for round_no in (1, 2) for key in BENCHMARKS]
    source = {'model': 'gpt-6.2-astra', 'cli_version': 'codex-cli 0.172.0',
              'reasoning_effort': 'high', 'service_tier': 'default', 'expected_rounds': 2,
              'samples': samples}
    sidecar.write_text(json.dumps(source))
    smoke = native_main_smoke(sidecar)
    assert smoke['expected_rounds'] == 2 and len(smoke['samples']) == 8
    assert [(s['round'], s['benchmark'], s['case']) for s in smoke['samples']] == [
        (r, key, f'000{r}') for r in (1, 2) for key in BENCHMARKS]
    assert all(s['token_usage']['output_tokens'] == 20 for s in smoke['samples'][:4])
    assert all(s['token_usage'] is None for s in smoke['samples'][4:])
    assert all(s[field] is None for s in smoke['samples'] for field in ('credits_consumed', 'credits_estimated', 'usd_estimated'))
    panel = render(dashboard_status(native_main_smoke=smoke)).split('<section id="native-main-smoke">')[1].split('</section>')[0]
    assert panel.count('<tr>') == 9
    assert panel.count('第 1 轮') == panel.count('第 2 轮') == 4
    assert panel.index('第 1 轮') < panel.index('第 2 轮') and '最多两轮八个样本' in panel
    assert '0.0000' not in panel
    source['expected_rounds'] = 3
    sidecar.write_text(json.dumps(source))
    with pytest.raises(ValueError, match='at most two rounds'):
        native_main_smoke(sidecar)
    source['expected_rounds'] = 2
    source['samples'].append({'round': 3, 'benchmark': 'physics_iq', 'case': '0003'})
    sidecar.write_text(json.dumps(source))
    with pytest.raises(ValueError, match='at most two rounds'):
        native_main_smoke(sidecar)


def test_native_weekly_delta_reads_each_rounds_own_telemetry(tmp_path):
    roots = [tmp_path/'run', tmp_path/'run/round2']
    source = tmp_path/'source.json'
    source.write_text(json.dumps({'root': str(roots[0]), 'roots': [str(root) for root in roots]}))
    for round_no, root in enumerate(roots, 1):
        telemetry = root/'telemetry'
        telemetry.mkdir(parents=True)
        before, after = telemetry/'before.json', telemetry/'after.json'
        for path, percent in ((before, 69), (after, 69+round_no-1)):
            path.write_text(json.dumps({'rate_limits': {'rateLimits': {'primary': {
                'usedPercent': percent, 'windowDurationMins': 10080, 'resetsAt': 123}}}}))
        (root/'progress.json').write_text(json.dumps({'samples': [{'benchmark': 'physics_iq',
            'case': '0001', 'quota_before': str(before), 'quota_after': str(after)}]}))
    assert native_smoke_weekly_deltas(source) == {(1, 'physics_iq', '0001'): 0, (2, 'physics_iq', '0001'): 1}
