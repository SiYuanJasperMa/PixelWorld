import json

import pytest


def test_matched_rbench_video_baseline_requires_protocol_and_complete_scope():
    from tools.build_main_suite_status import rbench_baseline_score
    from tools.evaluate_rbench_common_gpt61 import UPSTREAM
    source = {'model': 'test-model', 'scorer_sha256': 'a' * 64, 'manifest_sha256': 'b' * 64}
    report = {**source, 'protocol': 'rbench-common-official-gpt-branch-gpt61-sol-v1',
              'judge': JUDGE, 'official_code_commit': UPSTREAM, 'full_common50': True,
              'expected_cases': 50, 'scored_cases': 50, 'failed_cases': [], 'mean': 3.0,
              'official_combined_gpt_qwen_leaderboard_equivalent': False}
    assert rbench_baseline_score(report, source) == 0.5
    assert rbench_baseline_score({**report, 'scored_cases': 49}, source) is None
    assert rbench_baseline_score({**report, 'judge': 'different-judge'}, source) is None
    assert rbench_baseline_score({**report, 'official_combined_gpt_qwen_leaderboard_equivalent': True}, source) is None
    assert rbench_baseline_score(report, {**source, 'manifest_sha256': 'c' * 64}) is None


def test_published_baselines_remain_visible_with_empty_local_plan(tmp_path):
    from tools.build_main_suite_status import baseline_references
    bundle = tmp_path/'bundle'
    publication = bundle/'publication/pixelworld'
    publication.mkdir(parents=True)
    evaluation = bundle/'evaluation'
    evaluation.mkdir()
    metadata = {'models':[{'key':'seedance25','scores':{
        'physics_iq':{'kind':'published_reference','value':42.0},
        'pcve':{'kind':'published_reference','value':-0.1}}}]}
    (evaluation/'video_baselines.json').write_text(json.dumps(metadata))
    references = baseline_references({'video_baselines':[]}, publication, evaluation, {}, {})
    assert references['seedance25']['physics_iq'] == 42.0
    assert references['seedance25']['pcve'] == -0.1
    assert references['seedance25']['phygenbench'] is None


def test_current_evaluation_progress_uses_registered_source_without_stale_fallback(tmp_path):
    from tools.build_main_suite_status import current_video_evaluation_progress
    legacy=tmp_path/'maintenance_20261125';legacy.mkdir()
    (legacy/'progress_summary.json').write_text(json.dumps({'schema_version':1,'available_video_records':10}))
    assert current_video_evaluation_progress(tmp_path)['available_video_records']==10
    current=tmp_path/'current.json'
    current.write_text(json.dumps({'schema_version':1,'available_video_records':20,'private_path':'/hidden'}))
    (tmp_path/'current_quality_sources.json').write_text(json.dumps({'progress':str(current)}))
    result=current_video_evaluation_progress(tmp_path)
    assert result['available_video_records']==20 and 'private_path' not in result
    current.write_text('{}')
    assert current_video_evaluation_progress(tmp_path)=={}


def test_registered_phygen_baseline_requires_complete_matching_archive():
    from tools.build_main_suite_status import registered_phygen_baseline_score
    from tools.evaluate_phygen_three_stage_gpt55 import GPT61_VERSION
    source = {'model':'external-baseline','source_archive_manifest_sha256':'a'*64}
    report = {'version':GPT61_VERSION,'judge':JUDGE,'full160':True,'errors':[], 'cases':list(range(1,161)),
        'source_archive_manifest_sha256':'a'*64,
        'models':{'external-baseline':{'cases':160,'mean':0.6}}}
    assert registered_phygen_baseline_score(report, source) == 0.6
    assert registered_phygen_baseline_score({**report,'full160':False}, source) is None
    assert registered_phygen_baseline_score({**report,'judge':'another-judge'}, source) is None
    assert registered_phygen_baseline_score({**report,'errors':[{'case':1}]}, source) is None
    assert registered_phygen_baseline_score(report, {**source,'source_archive_manifest_sha256':'b'*64}) is None


def test_current_phygen_scores_fill_gaps_without_replacing_original_decisions():
    from tools.build_main_suite_status import merge_current_phygen_scores
    from tools.evaluate_phygen_three_stage_gpt55 import GPT61_VERSION
    original = {'version':GPT61_VERSION,'judge':JUDGE,'full160':True,'cases':list(range(1,161)),
                'models':{'original-model':{'cases':160,'mean':0.5}}}
    current = {**original,'models':{'original-model':{'cases':160,'mean':0.8},
                                   'new-model':{'cases':160,'mean':0.6},
                                   'partial-model':{'cases':159,'mean':None}}}
    merged = merge_current_phygen_scores(original, current)
    assert merged['models']['original-model']['mean'] == 0.5
    assert phygen_score(merged,'new-model') == (160,0.6)
    assert phygen_score(merged,'partial-model')[1] is None
    assert merge_current_phygen_scores(original,{**current,'judge':'different-judge'}) == original
    assert merge_current_phygen_scores(original,{**current,'full160':False}) == original

from tools.build_main_suite_status import (
    BENCHMARKS, JUDGE, appearance_scores, harness_progress, legacy_score, native_main_smoke,
    native_smoke_additional_samples, native_smoke_cumulative_quota, native_smoke_weekly_deltas,
    native_quota_study, native_sample_totals,
    native_experiment_cells,
    phygen_score, public_counts, rbench_score, render,
)


def test_additional_main_configs_use_registered_paths_and_exact_scope(tmp_path):
    from tools.build_main_suite_status import additional_main_configs
    config = {'case_ids': [f'{i:04d}' for i in range(1, 51)],
              'models': [{'key': 'gpt62-sol', 'model': 'traex/GLM-5.4', 'tracks': ['vision-agent']}]}
    path = tmp_path / 'active-model.json'
    path.write_text(json.dumps(config))
    registry = {'configured': [{'model_key': 'gpt62-sol', 'benchmark': 'rbench', 'config': 'active-model.json'}]}
    (tmp_path / 'additional_main_cohorts.json').write_text(json.dumps(registry))
    assert additional_main_configs(tmp_path)['gpt62-sol', 'rbench'].source == path
    config['case_ids'].pop()
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='scope'):
        additional_main_configs(tmp_path)


def test_additional_main_rejects_duplicate_cohort_and_vision_input(tmp_path):
    from tools.build_main_suite_status import additional_main_configs
    assert additional_main_configs(tmp_path) == {}
    config = {'case_ids': [f'{i:04d}' for i in range(1, 51)],
              'models': [{'key': 'gpt62-sol', 'model': 'traex/GLM-5.4', 'tracks': ['vision-input']}]}
    path = tmp_path / 'active-model.json'
    path.write_text(json.dumps(config))
    row = {'model_key': 'gpt62-sol', 'benchmark': 'rbench', 'config': 'active-model.json'}
    registry = tmp_path / 'additional_main_cohorts.json'
    registry.write_text(json.dumps({'configured': [row]}))
    with pytest.raises(ValueError, match='Vision-Agent'):
        additional_main_configs(tmp_path)
    config['models'][0]['tracks'] = ['vision-agent']
    path.write_text(json.dumps(config))
    registry.write_text(json.dumps({'configured': [row, row]}))
    with pytest.raises(ValueError, match='Duplicate'):
        additional_main_configs(tmp_path)


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
    row = {'scored': 49, 'expected': 50, 'full_common50': True, 'mean': 3.84}
    report = {'protocol': 'rbench-common-official-gpt-branch-gpt61-sol-v1',
              'judge': JUDGE, 'models': {'example': row}}
    assert rbench_score(report, 'example') == (49, None)
    row.update(scored=50, judge_errors=['0049'])
    assert rbench_score(report, 'example') == (50, None)
    row['judge_errors'] = []
    count, score = rbench_score(report, 'example')
    assert count == 50 and score == pytest.approx(0.71)
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


def test_opencode_live_counts_override_stale_snapshot_without_changing_claude(tmp_path):
    config, runs = tmp_path/'claude.json', tmp_path/'runs'
    runs.mkdir()
    config.write_text(json.dumps({'runs_root': str(runs)}))
    entry = {'config': str(config), 'registered_cases': 198, 'full_supervisor_started': True}
    (tmp_path/'dispatch.json').write_text(json.dumps({'models': {'gpt62-sol': {'opencode': entry, 'claude_code': entry}}}))
    (runs/'status.json').write_text(json.dumps({'cases': 198, 'groups': {'gpt62-sol/vision-agent': {
        'model_key': 'gpt62-sol', 'counts': {'complete': 2, 'running': 0, 'pending': 196}}}}))
    (tmp_path/'opencode_live_status.json').write_text(json.dumps({'updated_utc': '2026-11-24T12:00:00Z',
        'monitor_pid': 999, 'models': [
            {'model_key': 'gpt62-sol', 'expected': 198, 'counts': {'complete': 7, 'running': 1, 'pending': 190},
             'phase': 'full_running', 'full_released': True, 'supervisors': {'private': 'PRIVATE_PATH'}},
            {'model_key': 'deepseek-v41', 'expected': 198, 'counts': {'complete': 0, 'running': 1, 'pending': 197},
             'phase': 'canary_running', 'full_released': False, 'active_cases': ['PRIVATE_CASE']}]}))
    result = harness_progress(tmp_path)
    assert result['gpt62-sol']['opencode']['counts']['complete'] == 7
    assert result['gpt62-sol']['claude_code']['counts']['complete'] == 2
    assert result['deepseek-v41']['opencode']['state'] == 'canary_running'
    assert result['deepseek-v41']['opencode']['counts']['running'] == 1
    assert result['gpt62-sol']['opencode']['updated_utc'] == '2026-11-24T12:00:00Z'
    assert 'PRIVATE' not in json.dumps(result) and 'pid' not in json.dumps(result)


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
    for page in ('physics_iq_baseline.html', 'phygenbench.html', 'pcve_rigid.html'):
        assert f'<a href="{page}">' in html
        assert f'href="{page}?model=gpt61-sol"' in html
        assert f'href="{page}?model=gpt62-sol"' in html
    assert 'href="rbench_common_manipulation.html"' in html
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
    progress_path = roots[1]/'progress.json'
    progress = json.loads(progress_path.read_text())
    progress['execution_mode'] = 'parallel'
    progress_path.write_text(json.dumps(progress))
    assert native_smoke_weekly_deltas(source) == {(1, 'physics_iq', '0001'): 0}
    progress.pop('execution_mode')
    progress['samples'][0]['quota_attribution'] = 'shared_parallel_window'
    progress_path.write_text(json.dumps(progress))
    assert native_smoke_weekly_deltas(source) == {(1, 'physics_iq', '0001'): 0}


def test_native_parallel_round_uses_shared_summary_without_individual_quota(tmp_path):
    sidecar = tmp_path/'smoke.json'
    source = {'model': 'gpt-6.2-astra', 'expected_rounds': 2, 'samples': [
        {'round': 2, 'benchmark': key, 'case': '0002', 'state': 'complete',
         'duration_seconds': 123, 'token_usage': {'input_tokens': 2345, 'output_tokens': 123},
         'quota_weekly_consumed': 9, 'credits_consumed': 999}
        for key in BENCHMARKS], 'round_summaries': [
            {'round': 1, 'execution_mode': 'serial', 'weekly_change': 0, 'credits_change': 0,
             'balance': 'PRIVATE_BALANCE'},
            {'round': 2, 'execution_mode': 'parallel', 'weekly_change': 1, 'credits_change': None,
             'account': 'PRIVATE_ACCOUNT'}]}
    sidecar.write_text(json.dumps(source))
    smoke = native_main_smoke(sidecar)
    assert smoke['round_summaries'] == [
        {'round': 1, 'execution_mode': 'serial', 'weekly_change': 0, 'credits_change': 0},
        {'round': 2, 'execution_mode': 'parallel', 'weekly_change': 1, 'credits_change': None}]
    assert all(s['quota_weekly_consumed'] is None and s['credits_consumed'] is None
               and s['quota_attribution'] == 'shared_parallel_window' for s in smoke['samples'][4:])
    assert all(s['token_usage']['input_tokens'] == 2345 and s['duration_seconds'] == 123 for s in smoke['samples'][4:])
    assert 'PRIVATE' not in json.dumps(smoke)
    panel = render(dashboard_status(native_main_smoke=smoke)).split('<section id="native-main-smoke">')[1].split('</section>')[0]
    assert '第 1 轮 · 串行' in panel and '第 2 轮 · 并行' in panel
    assert '整轮周额度变化 1.00 百分点' in panel and 'Credits 余额差 待记录' in panel
    assert panel.count('整轮看汇总') == 8
    assert '2,345' in panel and '123.0 秒' in panel
    assert '999' not in panel and 'PRIVATE' not in panel


def test_extra_physics_smoke_is_separate_from_two_rounds_and_only_cumulative_quota_is_public(tmp_path):
    root, extra = tmp_path/'run', tmp_path/'run/extra_physics_0003'
    extra.mkdir(parents=True)
    telemetry = root/'telemetry'
    telemetry.mkdir()
    source_path = tmp_path/'source.json'
    source_path.write_text(json.dumps({'root': str(root)}))
    (root/'extra_samples.json').write_text(json.dumps({'samples': [
        {'benchmark': 'physics_iq', 'case': '0003', 'root': str(extra), 'pid': 123}]}))
    (extra/'progress.json').write_text(json.dumps({'samples': [
        {'benchmark': 'physics_iq', 'case': '0003', 'state': 'generating', 'duration_seconds': 42,
         'token_usage': {'input_tokens': 1234, 'cached_input_tokens': 1000, 'output_tokens': 234,
                         'private': 'PRIVATE_TOKEN_DATA'},
         'credits_consumed': 99, 'quota_weekly_consumed': 99, 'account': 'PRIVATE_ACCOUNT'}]}))
    for name, percent in (('physics_iq_before.json', 69), ('quota_live_now.json', 70)):
        (telemetry/name).write_text(json.dumps({'rate_limits': {'rateLimits': {
            'primary': {'usedPercent': percent, 'windowDurationMins': 10080, 'resetsAt': 123},
            'credits': {'balance': '100.0000'}}, 'accountId': 'PRIVATE_ACCOUNT'}}))
    sidecar = tmp_path/'smoke.json'
    sidecar.write_text(json.dumps({'model': 'gpt-6.2-astra', 'expected_rounds': 2, 'samples': [],
        'round_summaries': [{'round': 2, 'execution_mode': 'parallel', 'weekly_change': 1, 'credits_change': 0}]}))
    smoke = native_main_smoke(sidecar, source_path)
    assert smoke['expected_rounds'] == 2 and len(smoke['samples']) == 8
    assert len(smoke['additional_samples']) == 1
    sample = smoke['additional_samples'][0]
    assert sample['case'] == '0003' and 'round' not in sample
    assert sample['state'] == 'generating' and sample['token_usage']['output_tokens'] == 234
    assert sample['quota_weekly_consumed'] is sample['credits_consumed'] is None
    assert smoke['cumulative_quota'] == {'remaining_before': 31, 'remaining_latest': 30,
                                        'observed_change_pp': 1, 'credits_change': 0}
    encoded = json.dumps(smoke)
    assert 'PRIVATE' not in encoded and 'balance' not in encoded and str(root) not in encoded
    panel = render(dashboard_status(native_main_smoke=smoke)).split('<section id="native-main-smoke">')[1].split('</section>')[0]
    assert 'Physics-IQ 追加 0003' in panel and '并行阶段合计（含追加样本重叠）' in panel
    assert '周剩余 31% → 30%' in panel and '观测变化 1.00 百分点' in panel
    assert 'physics_iq_native_astra62_extra_0003%2Fgpt62-astra-native%2Fvision-agent%2Findex.json' in panel
    assert panel.count('<tbody>') == 2 and panel.count('<tr>') == 11
    (telemetry/'quota_live_now.json').write_text(json.dumps({'rate_limits': {'rateLimits': {
        'primary': {'usedPercent': 1, 'windowDurationMins': 10080, 'resetsAt': 456}}}}))
    assert native_smoke_cumulative_quota(source_path)['observed_change_pp'] is None
    outside = tmp_path/'outside'
    outside.mkdir()
    (root/'extra_samples.json').write_text(json.dumps({'samples': [
        {'benchmark': 'physics_iq', 'case': '0003', 'root': str(outside)}]}))
    assert native_smoke_additional_samples(source_path) == []


def test_quota_study_refreshes_closed_manifest_and_keeps_original_nine_in_totals(tmp_path):
    primary = tmp_path/'primary'
    quota, extra = primary/'quota5', primary/'extra_physics_0003'
    quota.mkdir(parents=True)
    extra.mkdir()
    source_path = tmp_path/'source.json'
    source_path.write_text(json.dumps({'root': str(primary), 'quota5_root': str(quota)}))
    (primary/'extra_samples.json').write_text(json.dumps({'samples': [
        {'benchmark': 'physics_iq', 'case': '0003', 'root': str(extra)}]}))
    (extra/'progress.json').write_text(json.dumps({'samples': [
        {'benchmark': 'physics_iq', 'case': '0003', 'state': 'complete',
         'token_usage': {'input_tokens': 200, 'cached_input_tokens': 100, 'output_tokens': 20}}]}))
    run = quota/'runs/physics_iq/0004'
    run.mkdir(parents=True)
    (run/'run_manifest.json').write_text(json.dumps({'status': 'complete', 'duration_seconds': 10,
        'token_usage': {'input_tokens': 500, 'cached_input_tokens': 400, 'output_tokens': 50,
                        'private': 'PRIVATE_WIRE_DATA'}, 'auth': 'PRIVATE_AUTH'}))
    (quota/'progress.json').write_text(json.dumps({'status': 'draining', 'model': 'gpt-6.2-astra',
        'codex_version': '0.172.0', 'reasoning_effort': 'high', 'service_tier': 'default', 'execution_mode': 'parallel',
        'quota_study': {'baseline_remaining_percent': 31, 'target_remaining_percent': 26,
            'current_remaining_percent': 30, 'consumed_percentage_points': 1, 'max_concurrency': 16,
            'active': 1, 'completed': 0, 'failed': 0, 'baseline_samples': 9,
            'stop_reason': 'reserve_for_inflight', 'balance': 'PRIVATE_BALANCE', 'account': 'PRIVATE_ACCOUNT'},
        'samples': [{'benchmark': 'physics_iq', 'case': '0004', 'state': 'running', 'run': str(run),
                     'config': 'PRIVATE_CONFIG', 'pid': 123, 'credits_consumed': 99},
                    {'benchmark': 'phygenbench', 'case': '0003', 'state': 'running', 'token_usage': None}]}))
    sidecar = tmp_path/'smoke.json'
    sidecar.write_text(json.dumps({'model': 'gpt-6.2-astra', 'expected_rounds': 2,
        'samples': [{'round': round_no, 'benchmark': key, 'case': f'000{round_no}', 'state': 'complete',
                    'token_usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 10}}
                   for round_no in (1, 2) for key in BENCHMARKS]}))
    smoke = native_main_smoke(sidecar, source_path)
    assert len(smoke['samples']) == 8 and len(smoke['additional_samples']) == 1
    study = smoke['quota_study']
    assert study['study_completed'] == 1 and study['current_active'] == 1 and study['baseline_samples'] == 9
    first = study['samples'][0]
    assert first['state'] == 'complete' and first['token_usage']['output_tokens'] == 50
    assert first['duration_seconds'] == 10 and first['credits_consumed'] is first['quota_weekly_consumed'] is None
    totals = smoke['cumulative_totals']
    assert totals['completed'] == 10 and totals['registered_samples'] == 11
    assert totals['samples_with_usage'] == 10 and totals['samples_without_usage'] == 1
    assert totals['token_usage']['input_tokens'] == 1500
    assert totals['token_usage']['cached_input_tokens'] == 1140 and totals['token_usage']['output_tokens'] == 150
    encoded = json.dumps(smoke)
    assert 'PRIVATE' not in encoded and str(primary) not in encoded and 'balance' not in encoded
    html = render(dashboard_status(native_main_smoke=smoke))
    panel = html.split('<section id="native-quota-study">')[1].split('</section>')[0]
    assert '周剩余目标 31% → 26%' in panel and '并发上限 16' in panel
    assert '累计完成 <b>10</b> / 已登记 11' in panel and '含前期 9 条' in panel
    assert 'Input 1,500' in panel and 'Cached input 1,140' in panel and 'Output 150' in panel
    assert '为运行中样本预留额度' in panel and '0.172.0' in panel
    assert 'physics_iq_native_astra62_quota5%2Fgpt62-astra-native%2Fvision-agent%2Findex.json' in panel
    assert 'phygenbench_native_astra62_quota5%2Fgpt62-astra-native%2Fvision-agent%2Findex.json' in panel
    outside = tmp_path/'outside'
    outside.mkdir()
    source_path.write_text(json.dumps({'root': str(primary), 'quota5_root': str(outside)}))
    assert native_quota_study(source_path) == {}


def test_native_token_totals_leave_unreported_usage_unknown():
    totals = native_sample_totals([{'case': '0001', 'state': 'running', 'token_usage': None},
                                   {'case': '0002', 'state': 'pending', 'token_usage': None}])
    assert totals['registered_samples'] == 1 and totals['samples_without_usage'] == 1
    assert all(value is None for value in totals['token_usage'].values())


def test_native_formal_row_deduplicates_batches_without_inventing_full_scores():
    smoke = {'samples': [{'benchmark': 'physics_iq', 'case': '0001', 'state': 'complete'},
                          {'benchmark': 'physics_iq', 'case': '0002', 'state': 'failed'}],
             'additional_samples': [{'benchmark': 'physics_iq', 'case': '0003', 'state': 'complete'}],
             'quota_study': {'samples': [{'benchmark': 'physics_iq', 'case': '0001', 'state': 'failed'},
                {'benchmark': 'physics_iq', 'case': '0002', 'state': 'running'},
                {'benchmark': 'physics_iq', 'case': '0004', 'state': 'complete'}]}}
    cells = native_experiment_cells(smoke)
    assert cells['physics_iq']['counts'] == {'complete': 3, 'running': 1, 'exhausted': 0, 'pending': 194}
    assert all(row['score'] is None and row['scored'] == 0 for row in cells.values())
    assert [cells[key]['expected'] for key in BENCHMARKS] == [198, 50, 160, 129]
    html = render(dashboard_status({'gpt62-astra-native': cells}, native_main_smoke=smoke))
    assert 'class="native-row"' in html and 'Codex 0.172.0 · high · default' in html
    assert 'physics_iq_baseline.html?model=gpt62-astra-native' in html
    assert '<details id="native-history"><summary>' in html
    assert '<b>已处理 3 / 198</b>' in html
    assert '成功 3 · 确认失败 0' in html
def test_native_pcve_keeps_the_official_zero_based_case_ids():
    from tools.build_main_suite_status import native_experiment_cells
    rows = [{'benchmark':'pcve','case':case,'state':'complete'}
            for case in ('pcve_0000','pcve_0001','pcve_0128','pcve_0129')]
    cells = native_experiment_cells({'samples':rows})
    assert cells['pcve']['counts']['complete'] == 3
    assert cells['pcve']['counts']['pending'] == 126


def test_native_formal_cells_keep_real_completion_time():
    from tools.build_main_suite_status import native_experiment_cells
    smoke = {'quota_study': {'samples': [
        {'benchmark':'physics_iq','case':'0004','state':'complete','completed_unix':1795525000},
        {'benchmark':'physics_iq','case':'0005','state':'generating','completed_unix':1795526000}]}}
    cell = native_experiment_cells(smoke)['physics_iq']
    assert cell['last_delivery_utc'] == '2026-11-24T12:56:40+00:00'


def test_parallel_main_counts_worker_pids_on_the_worker_and_keeps_master_cases(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from tools import build_main_suite_status as mod
    model = SimpleNamespace(key='model')
    fields = dict(protocol='frozen', inputs_root=tmp_path/'inputs', models=(model,),
                  max_attempts=1, max_total_attempts=5, output_token_budget=1000000,
                  filesystem_policy='case-only-v1', harness_variant='baseline')
    original = SimpleNamespace(source=tmp_path/'original.json', runs_root=tmp_path/'original',
                               case_ids=('0001','0002','0003'), **fields)
    worker = SimpleNamespace(source=tmp_path/'worker.json', runs_root=tmp_path/'worker',
                             case_ids=('0001','0002'), **fields)
    worker.runs_root.mkdir()
    (worker.runs_root/'status.json').write_text(json.dumps({
        'updated_utc':datetime.now(timezone.utc).isoformat(),
        'groups':{'model/vision-agent':{'running_cases':['0002']}}}))
    monkeypatch.setattr(mod, 'load_config', lambda _:worker)
    def inspect(config, _, track, case):
        if case == '0001': return {'state':'complete','completed_utc':'2026-11-24T12:00:00+00:00'}
        return {'state':'exhausted' if config is worker else 'running'}
    monkeypatch.setattr(mod, 'inspect', inspect)
    mappings = {'main':{'source_config':str(original.source),'canonical_config':str(worker.source)}}
    result = mod.count_parallel_config(original, model, 3, mappings)
    assert result['counts'] == {'complete':1,'running':2,'pending':0,'exhausted':0}


def test_full_scope_fresh_worker_snapshot_does_not_rescan_historical_attempts(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from tools import build_main_suite_status as mod
    model = SimpleNamespace(key='model')
    config = SimpleNamespace(source=tmp_path/'config.json', runs_root=tmp_path/'runs',
        case_ids=('0001','0002'), protocol='frozen', inputs_root=tmp_path/'inputs', models=(model,),
        max_attempts=1, max_total_attempts=5, output_token_budget=1000000,
        filesystem_policy='case-only-v1', harness_variant='baseline')
    config.runs_root.mkdir()
    (config.runs_root/'status.json').write_text(json.dumps({'cases':2,
        'updated_utc':datetime.now(timezone.utc).isoformat(), 'groups':{'model/vision-agent':{
            'model_key':'model', 'counts':{'complete':1,'running':1},
            'latest_completion':['2026-11-24T12:00:00+00:00','0001']}}}))
    monkeypatch.setattr(mod, 'load_config', lambda _:config)
    monkeypatch.setattr(mod, 'inspect', lambda *a: (_ for _ in ()).throw(AssertionError('Unexpected rescan')))
    result = mod.count_parallel_config(config, model, 2, {'x':{
        'source_config':str(config.source),'canonical_config':str(config.source)}})
    assert result['counts'] == {'complete':1,'running':1,'pending':0,'exhausted':0}
    assert result['last_delivery_utc'] == '2026-11-24T12:00:00+00:00'


def test_harness_worker_sidecar_overrides_only_its_verified_models(tmp_path):
    from tools.build_main_suite_status import harness_progress
    (tmp_path/'harness_worker_live_status.json').write_text(json.dumps({
        'schema_version':'pixelworld-harness-worker-live-v1','models':[
            {'model':'gpt62-sol','cli_backend':'claude_code','expected':198,
             'counts':{'complete':11,'running':2,'pending':185,'exhausted':0},
             'phase':'full_running','updated_utc':'2026-11-25T02:00:00+00:00',
             'pid':123,'private_path':'/private/example'}]}))
    result = harness_progress(tmp_path)['gpt62-sol']['claude_code']
    assert result['counts']['running'] == 2
    assert result['full_released'] is True
    assert 'pid' not in result and 'private_path' not in result


def test_migrated_harness_does_not_read_retired_master_counters(tmp_path):
    from tools.build_main_suite_status import harness_progress
    (tmp_path/'opencode_live_status.json').write_text(json.dumps({'models':[
        {'model_key':'gpt62-sol','expected':198,'counts':{
            'complete':17,'running':1,'pending':180,'exhausted':1}}]}))
    current = {'complete':24,'running':2,'pending':172,'exhausted':0}
    (tmp_path/'harness_worker_live_status.json').write_text(json.dumps({
        'schema_version':'pixelworld-harness-worker-live-v1','models':[
            {'model':'gpt62-sol','cli_backend':'opencode','expected':198,
             'counts':current,'phase':'full_running'}]}))
    assert harness_progress(tmp_path)['gpt62-sol']['opencode']['counts'] == current


def test_native_partial_physical_coverage_does_not_claim_full_score():
    from tools.build_main_suite_status import native_experiment_cells
    smoke = {'samples':[{'benchmark':'physics_iq','case':'0001','state':'complete'}]}
    progress = {'groups':[
        {'model_key':'gpt62-astra-native','benchmark':'physics_iq_720p24fps',
         'candidate_type':'accepted','expected_samples':198,'available_videos':1,
         'unique_video_hashes':1,'physical_bindings':1},
        {'model_key':'gpt62-astra-native','benchmark':'physics_iq_720p24fps',
         'candidate_type':'intermediate','expected_samples':198,'physical_bindings':40}]}
    cell = native_experiment_cells(smoke, progress)['physics_iq']
    assert cell['scored'] == 1
    assert cell['score'] is None and cell['expected'] == 198


def test_evaluation_projection_keeps_distinct_freshness_timestamps():
    from tools.build_main_suite_status import project_all_video_evaluation_progress
    result = project_all_video_evaluation_progress({'schema_version':1,
        'updated_utc':'2026-11-25T01:00:00Z','registry_updated_utc':'2026-11-25T00:00:00Z',
        'services':[{'kind':'pcve_physical','workers':4,
                     'workers_verified_utc':'2026-11-25T00:59:00Z',
                     'latest_score_utc':'2026-11-25T00:59:50Z','pid':123}]})
    assert result['registry_updated_utc'] == '2026-11-25T00:00:00Z'
    assert result['services'][0]['workers_verified_utc'] == '2026-11-25T00:59:00Z'
    assert result['services'][0]['latest_score_utc'] == '2026-11-25T00:59:50Z'
    assert 'pid' not in result['services'][0]


def test_all_video_progress_keeps_record_hash_and_binding_units_separate():
    from tools.build_main_suite_status import project_all_video_evaluation_progress
    report = {'schema_version':1,'updated_utc':'2026-11-25T02:00:00Z','collection_complete':False,
        'available_video_records':14000,'registered_new_score_bindings':3363,'pid':123,'path':'/private/producer',
        'services':[{'kind':'appearance','phase':'judging','workers':32,'rated_video_hashes':3310,
                     'unique_complete_videos':5301,'pid':99,'source_path':'/private/score'},
                    {'kind':'phygen_physical','workers':32,'completed':1197,'pending':1062}],
        'groups':[{'benchmark':'physics_iq','model_key':'gpt62-sol','condition':'main','candidate_type':'accepted',
                   'available_videos':41,'unique_video_hashes':40,'expected_samples':198,
                   'physical_bindings':20,'appearance_bindings':41,'path':'/private/inputs','score':9.9}],
        'native41':{'accepted_videos':41,'retained_candidate_videos':99,'unique_video_hashes':77,
                    'appearance_bindings':140,'physical_bindings':75,'account':'PRIVATE_ACCOUNT'}}
    progress = project_all_video_evaluation_progress(report)
    assert 'PRIVATE' not in json.dumps(progress) and '/private' not in json.dumps(progress)
    assert 'pid' not in json.dumps(progress) and 'score' not in progress['groups'][0]
    assert progress['available_video_records'] == 14000
    assert progress['services'][0]['unique_complete_videos'] == 5301
    assert progress['native41']['appearance_bindings'] == 140
    old_photo = {'gpt61-sol':{'physics_iq':{'score':4.42,'cases':198,'expected':198,'no_evidence':0}}}
    data = dashboard_status(all_video_evaluation_progress=progress, photographic_scores=old_photo,
                            photographic_progress={'completed_calls':976,'expected_calls':976})
    html = render(data)
    panel = html.split('<section id="all-video-evaluation-progress">')[1].split('</section>')[0]
    assert '已发现视频记录 14,000' in panel and '画质队列独立视频 5,301' in panel
    assert '新增评分绑定 3,363' in panel and '已评级独立哈希 3,310 / 5,301' in panel
    assert '尚非全库完成' in panel and '条候选记录' in panel and '99' in panel
    assert '画质评分绑定 140' in panel and '物理评分绑定 75' in panel
    assert 'metadata/all_video_evaluation_progress.json' in panel and '<details>' in panel
    assert '<b>4.42</b>' in html and '976 / 976' in html


def test_progress_counts_terminal_failures_as_processed_and_distinguishes_pending():
    from tools.build_main_suite_status import generation_bar,generation_phase
    closed={'complete':40,'exhausted':10,'running':0,'pending':0}
    assert generation_phase(closed,50)=='已结束（含失败）'
    bar=generation_bar(closed,50)
    assert 'data-state="exhausted"' in bar and 'background:#dc2626' in bar
    assert 'width:20.00%' in bar and '确认失败 10' in bar
    assert generation_phase({'complete':40,'exhausted':0,'running':1,'pending':9},50)=='运行中'
    assert generation_phase({'complete':40,'exhausted':0,'running':0,'pending':10},50)=='待继续生成'
    assert generation_phase({'complete':50,'exhausted':0,'running':0,'pending':0},50)=='全部成功'


def test_paper_roster_retains_new_astra_and_retires_old_astra():
    from tools.build_main_suite_status import ROSTER
    assert 'gpt6-astra' not in ROSTER
    assert 'gpt62-astra-native' in ROSTER


def test_retired_glm_is_ignored_without_loading_old_config(tmp_path):
    from tools.build_main_suite_status import additional_main_configs
    (tmp_path/"additional_main_cohorts.json").write_text(json.dumps({"configured":[{"model_key":"glm54","benchmark":"rbench","config":"retired.json"}]}))
    assert additional_main_configs(tmp_path)=={}
