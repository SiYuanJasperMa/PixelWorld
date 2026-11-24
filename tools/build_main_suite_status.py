"""Build a public-safe progress snapshot for the revised four-benchmark suite."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
from urllib.parse import quote

from pixelworld.config import load_config
from pixelworld.supervisor import inspect
from pixelworld.util import atomic_json, utc_now
from tools.watch_rbench_common_scores import configs as rbench_configs

BENCHMARKS = {
    'physics_iq': ('physics_iq_720p24fps', 198, 'Verified'),
    'rbench': ('rbench_common_manipulation', 50, 'GPT branch'),
    'phygenbench': ('phygenbench_t2v_main720p24fps', 160, 'Overall'),
    'pcve': ('pcve_standard720p24fps', 129, 'PES'),
}
ROSTER = ('gpt62-sol', 'gpt61-sol', 'gpt6-astra', 'gpt56-sol', 'gpt56-terra',
          'gpt56-luna', 'deepseek-v41', 'seed21-pro')
COUNT_FIELDS = ('complete', 'running', 'pending', 'exhausted')
JUDGE = 'traex/GPT-6.1-Sol'
TRACK = 'vision-agent'
SMOKE_TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                      'output_tokens', 'reasoning_output_tokens', 'total_tokens')
SMOKE_STATES = ('pending', 'waiting', 'generating', 'running', 'validating',
                'complete', 'finished', 'failed', 'blocked', 'skipped')
NATIVE_RUN_STATES = ('generating', 'running', 'validating', 'complete', 'finished', 'failed')


def native_smoke_tokens(usage):
    return ({field: value for field in SMOKE_TOKEN_FIELDS
             if (value := finite_score(usage.get(field))) is not None and value >= 0}
            if isinstance(usage, dict) else None)


def native_completed_utc(row):
    value = finite_score(row.get('completed_unix'))
    if value is not None:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    value = row.get('completed_utc')
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
        except ValueError:
            pass
    return None


def native_quota_study(source_path):
    """Project the registered quota sampler without exposing routes or account data."""
    source = read(source_path)
    if not isinstance(source.get('root'), str) or not isinstance(source.get('quota5_root'), str):
        return {}
    primary, root = Path(source['root']).resolve(), Path(source['quota5_root']).resolve()
    if root == primary or not root.is_relative_to(primary):
        return {}
    progress = read(root/'progress.json')
    if not progress:
        return {}
    def choice(value, values):
        return value if value in values else None
    version = progress.get('codex_version')
    version = version if isinstance(version, str) and re.fullmatch(r'(?:codex-cli )?\d+\.\d+\.\d+', version) else None
    study = progress.get('quota_study') or {}
    result = {'model': choice(progress.get('model'), ('gpt-6.2-astra',)), 'cli_version': version,
        'reasoning_effort': choice(progress.get('reasoning_effort'), ('low', 'medium', 'high', 'xhigh')),
        'service_tier': choice(progress.get('service_tier'), ('default', 'fast', 'flex', 'priority')),
        'execution_mode': choice(progress.get('execution_mode'), ('parallel',)),
        'status': choice(progress.get('status'), (*SMOKE_STATES, 'draining', 'stopping', 'stopped', 'paused', 'target_reached')),
        'stop_reason': choice(study.get('stop_reason'), ('target_reached', 'reserve_for_inflight',
            'quota_unavailable', 'quota_window_changed', 'memory_pressure', 'all_cases_processed', 'interrupted')),
        'samples': []}
    for field in ('baseline_remaining_percent', 'target_remaining_percent', 'current_remaining_percent', 'consumed_percentage_points'):
        value = finite_score(study.get(field))
        result[field] = value if value is not None and 0 <= value <= 100 else None
    for field in ('max_concurrency', 'active', 'completed', 'failed', 'baseline_samples'):
        value = study.get(field)
        result[field] = value if type(value) is int and value >= 0 and (field != 'max_concurrency' or value <= 16) else None
    identities = set()
    for row in progress.get('samples', []):
        benchmark, case = row.get('benchmark'), row.get('case')
        if (benchmark not in BENCHMARKS or not isinstance(case, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', case) or case.startswith(('hf_', 'ghp_'))):
            continue
        if (benchmark, case) in identities:
            raise ValueError('Duplicate quota study case')
        identities.add((benchmark, case))
        current = dict(row)
        name = row.get('run')
        if isinstance(name, str):
            run = Path(name).resolve()
            if run.is_relative_to(root):
                manifest = read(run/'run_manifest.json')
                if manifest.get('status') in ('complete', 'failed'):
                    current.update(state=manifest['status'], token_usage=manifest.get('token_usage'),
                                   duration_seconds=manifest.get('duration_seconds'))
        result['samples'].append({'benchmark': benchmark, 'case': case,
            'state': choice(current.get('state'), SMOKE_STATES),
            'completed_utc': native_completed_utc(current),
            'token_usage': native_smoke_tokens(current.get('token_usage')),
            'duration_seconds': finite_score(current.get('duration_seconds')),
            'quota_attribution': 'shared_parallel_window', 'quota_weekly_consumed': None, 'credits_consumed': None})
    totals = native_sample_totals(result['samples'])
    result.update(study_completed=totals['completed'], current_active=totals['active'],
                  token_totals=totals['token_usage'], samples_with_usage=totals['samples_with_usage'])
    return result


def native_sample_totals(samples):
    records = [row for row in samples if row.get('case') and row.get('state') in NATIVE_RUN_STATES]
    known = [row.get('token_usage') for row in records if row.get('token_usage')]
    totals = {}
    for field in SMOKE_TOKEN_FIELDS:
        values = [usage[field] for usage in known if field in usage]
        totals[field] = sum(values) if values else None
    return {'registered_samples': len(records),
        'completed': sum(row.get('state') in ('complete', 'finished') for row in records),
        'failed': sum(row.get('state') == 'failed' for row in records),
        'active': sum(row.get('state') in ('running', 'generating', 'validating') for row in records),
        'samples_with_usage': len(known), 'samples_without_usage': len(records)-len(known), 'token_usage': totals}


def native_experiment_cells(smoke):
    """Count unique official case identities across native batches, keeping scores unavailable."""
    rows = smoke.get('samples', []) + smoke.get('additional_samples', []) + smoke.get('quota_study', {}).get('samples', [])
    cells = {}
    for key, (_, expected, metric) in BENCHMARKS.items():
        cases = {}
        deliveries = []
        for row in rows:
            if row.get('benchmark') != key or not isinstance(row.get('case'), str):
                continue
            match = re.search(r'(\d+)$', row['case'])
            lower, upper = (0, expected-1) if key == 'pcve' else (1, expected)
            if not match or not lower <= int(match[1]) <= upper:
                continue
            case = int(match[1])
            state = row.get('state')
            rank = 0 if state in ('complete', 'finished') else 1 if state in ('running', 'generating', 'validating') else 3 if state in ('failed', 'blocked') else 2
            cases[case] = min(cases.get(case, 4), rank)
            if rank == 0 and (completed := native_completed_utc(row)):
                deliveries.append(completed)
        counts = Counter(cases.values())
        cells[key] = {'counts': {'complete': counts[0], 'running': counts[1], 'exhausted': counts[3],
                                'pending': expected-counts[0]-counts[1]-counts[3]},
                      'expected': expected, 'scored': 0, 'score': None, 'metric': metric,
                      'last_delivery_utc': max(deliveries, default=None)}
    return cells


def native_smoke_additional_samples(source_path):
    source = read(source_path)
    if not isinstance(source.get('root'), str):
        return []
    root = Path(source['root']).resolve()
    entries = read(root/'extra_samples.json').get('samples', [])
    if len(entries) > 1:
        raise ValueError('Only one additional Physics-IQ smoke is registered')
    result = []
    for entry in entries:
        case, name = entry.get('case'), entry.get('root')
        if (entry.get('benchmark') != 'physics_iq' or not isinstance(case, str)
            or not re.fullmatch(r'[0-9]{4}', case) or not isinstance(name, str)):
            continue
        extra = Path(name).resolve()
        if not extra.is_relative_to(root) or extra == root:
            continue
        records = [row for row in read(extra/'progress.json').get('samples', [])
                   if (row.get('benchmark'), row.get('case')) == ('physics_iq', case)]
        if len(records) > 1:
            raise ValueError('Duplicate additional Physics-IQ smoke')
        row = records[0] if records else {}
        result.append({'benchmark': 'physics_iq', 'case': case,
            'state': row.get('state') if row.get('state') in SMOKE_STATES else None,
            'token_usage': native_smoke_tokens(row.get('token_usage')),
            'duration_seconds': finite_score(row.get('duration_seconds')),
            'quota_attribution': 'shared_parallel_window',
            'quota_weekly_consumed': None, 'credits_consumed': None,
            'credits_estimated': None, 'usd_estimated': None})
    return result


def native_smoke_cumulative_quota(source_path):
    source = read(source_path)
    if not isinstance(source.get('root'), str):
        return {}
    telemetry = Path(source['root']).resolve()/'telemetry'
    snapshots = [(read(telemetry/name).get('rate_limits') or {}).get('rateLimits') or {}
                 for name in ('physics_iq_before.json', 'quota_live_now.json')]
    if not all(snapshots):
        return {}
    before, latest = [(row.get('primary') or {}) for row in snapshots]
    start, end = finite_score(before.get('usedPercent')), finite_score(latest.get('usedPercent'))
    start = start if start is not None and 0 <= start <= 100 else None
    end = end if end is not None and 0 <= end <= 100 else None
    same_window = (before.get('windowDurationMins') == latest.get('windowDurationMins') == 10080
                   and before.get('resetsAt') is not None and before.get('resetsAt') == latest.get('resetsAt'))
    credits = None
    try:
        credits = finite_score(float(Decimal(str((snapshots[0].get('credits') or {}).get('balance')))
                                    - Decimal(str((snapshots[1].get('credits') or {}).get('balance')))))
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return {'remaining_before': 100-start if start is not None else None,
            'remaining_latest': 100-end if end is not None else None,
            'observed_change_pp': end-start if same_window and start is not None and end is not None and end >= start else None,
            'credits_change': credits}


def native_smoke_weekly_deltas(source_path):
    """Read private receipts locally; return only matched weekly usage changes."""
    source = read(source_path)
    roots = source.get('roots') or [source.get('root')]
    if not isinstance(roots, list) or len(roots) > 2 or any(not isinstance(root, str) for root in roots):
        return {}
    result = {}
    for root_round, name in enumerate(roots, 1):
        root = Path(name).resolve()
        telemetry = (root/'telemetry').resolve()
        progress = read(root/'progress.json')
        if progress.get('execution_mode') == 'parallel':
            continue
        for sample in progress.get('samples', []):
            if sample.get('quota_attribution') == 'shared_parallel_window':
                continue
            round_no = sample.get('round', root_round)
            if sample.get('benchmark') not in BENCHMARKS or type(round_no) is not int or round_no != root_round:
                continue
            windows = []
            for key in ('quota_before', 'quota_after'):
                name = sample.get(key)
                if not isinstance(name, str):
                    break
                path = Path(name)
                path = (path if path.is_absolute() else root/path).resolve()
                if not path.is_relative_to(telemetry):
                    break
                snapshot = read(path).get('rate_limits') or {}
                windows.append((snapshot.get('rateLimits') or {}).get('primary') or {})
            if len(windows) != 2:
                continue
            before, after = windows
            if (before.get('windowDurationMins') != 10080 or
                (before.get('windowDurationMins'), before.get('resetsAt')) !=
                (after.get('windowDurationMins'), after.get('resetsAt')) or before.get('resetsAt') is None):
                continue
            start, end = finite_score(before.get('usedPercent')), finite_score(after.get('usedPercent'))
            if start is not None and end is not None and 0 <= start <= end <= 100:
                result[(round_no, sample.get('benchmark'), sample.get('case'))] = end - start
    return result


def native_main_smoke(path, source_path=None):
    """Publish only the native smoke's experiment metadata and consumption deltas."""
    source = read(path)
    if not source:
        return {}
    expected_rounds = source.get('expected_rounds', 1)
    if type(expected_rounds) is not int or expected_rounds not in (1, 2):
        raise ValueError('Native smoke permits at most two rounds')
    def choice(value, allowed):
        return value if value in allowed else None
    version = source.get('cli_version')
    version = version if isinstance(version, str) and re.fullmatch(r'(?:codex-cli )?\d+\.\d+\.\d+', version) else None
    result = {'model': choice(source.get('model'), ('gpt-6.2-astra',)), 'cli_version': version,
              'reasoning_effort': choice(source.get('reasoning_effort'), ('low', 'medium', 'high', 'xhigh')),
              'service_tier': choice(source.get('service_tier'), ('default', 'fast', 'flex', 'priority')),
              'status': choice(source.get('status'), ('pending', 'running', 'complete', 'finished', 'failed', 'blocked')),
              'samples': [], 'round_summaries': [],
              'additional_samples': native_smoke_additional_samples(source_path) if source_path else [],
              'cumulative_quota': native_smoke_cumulative_quota(source_path) if source_path else {},
              'quota_study': native_quota_study(source_path) if source_path else {}}
    summaries = {}
    for row in source.get('round_summaries', []):
        round_no = row.get('round', 1)
        if type(round_no) is not int or round_no not in (1, 2):
            raise ValueError('Native smoke permits at most two rounds')
        if round_no in summaries:
            raise ValueError('Duplicate native smoke round summary')
        summaries[round_no] = {'round': round_no,
            'execution_mode': choice(row.get('execution_mode'), ('serial', 'parallel')),
            'weekly_change': finite_score(row.get('weekly_change')),
            'credits_change': finite_score(row.get('credits_change'))}
        expected_rounds = max(expected_rounds, round_no)
    result['round_summaries'] = [summaries[key] for key in sorted(summaries)]
    samples = {}
    deltas = native_smoke_weekly_deltas(source_path) if source_path else {}
    for row in source.get('samples', []):
        key = row.get('benchmark')
        if key not in BENCHMARKS:
            continue
        round_no = row.get('round', 1)
        if type(round_no) is not int or round_no not in (1, 2):
            raise ValueError('Native smoke permits at most two rounds')
        if (round_no, key) in samples:
            raise ValueError('Duplicate native smoke round and benchmark')
        samples[(round_no, key)] = row
        expected_rounds = max(expected_rounds, round_no)
    result['expected_rounds'] = expected_rounds
    for round_no, key in ((round_no, key) for round_no in range(1, expected_rounds+1) for key in BENCHMARKS):
        row = samples.get((round_no, key), {})
        case = row.get('case')
        case = case if isinstance(case, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', case) and not case.startswith(('hf_', 'ghp_')) else None
        usage = native_smoke_tokens(row.get('token_usage'))
        sample = {'round': round_no, 'benchmark': key, 'case': case,
            'state': choice(row.get('state'), SMOKE_STATES),
            'duration_seconds': finite_score(row.get('duration_seconds')), 'token_usage': usage,
            'quota_attribution': choice(row.get('quota_attribution'), ('shared_parallel_window', 'individual_serial_window')),
            **{field: finite_score(row.get(field)) for field in ('quota_weekly_consumed', 'credits_consumed', 'credits_estimated', 'usd_estimated')}}
        if (sample['quota_attribution'] == 'shared_parallel_window' or
            summaries.get(round_no, {}).get('execution_mode') == 'parallel'):
            sample.update(quota_attribution='shared_parallel_window', quota_weekly_consumed=None, credits_consumed=None)
        elif sample['quota_weekly_consumed'] is None:
            sample['quota_weekly_consumed'] = deltas.get((round_no, key, case))
        result['samples'].append(sample)
    result['cumulative_totals'] = native_sample_totals(result['samples'] + result['additional_samples']
                                                     + result['quota_study'].get('samples', []))
    return result


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def finite_score(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def count_config(config, model, expected):
    if len(config.case_ids) != expected or len(set(config.case_ids)) != expected:
        raise ValueError(f'Configured scope does not contain exactly {expected} unique cases')
    states = [inspect(config, model, TRACK, case) for case in config.case_ids]
    counts = Counter(row['state'] for row in states)
    latest = max((row.get('completed_utc') for row in states if row.get('completed_utc')), default=None)
    return {'counts': {key: counts[key] for key in COUNT_FIELDS}, 'last_delivery_utc': latest}


def public_counts(group, expected):
    values = {key: group.get('counts', {}).get(key, 0) for key in COUNT_FIELDS}
    if any(type(value) is not int or value < 0 for value in values.values()) or sum(values.values()) > expected:
        raise ValueError('Invalid published case counters')
    recent = group.get('latest_completion') or []
    return {'counts': values, 'last_delivery_utc': recent[0] if recent else None}


def legacy_score(record, expected, metric):
    score = record.get('score') or {}
    if record.get('full_scope_scored') is not True or score.get('scope') != expected:
        return None
    return finite_score(score.get('metrics', {}).get(metric))


def phygen_score(report, model):
    row = report.get('models', {}).get(model, {})
    if (report.get('version'), report.get('judge'), report.get('full160')) != (
        'phygen-gpt61-sol-three-stage-mcq-v1', JUDGE, True,
    ):
        return 0, None
    if report.get('cases') != list(range(1, 161)):
        return 0, None
    scored = row.get('cases', 0)
    return scored, finite_score(row.get('mean')) if scored == 160 else None


def rbench_score(report, model):
    if (report.get('protocol'), report.get('judge')) != ('rbench-common-official-gpt-branch-gpt61-sol-v1', JUDGE):
        return 0, None
    row = report.get('models', {}).get(model, {})
    scored = row.get('scored', 0)
    full = (row.get('full_common50') is True and scored == row.get('expected') == 50
            and not any(row.get(field) for field in ('waiting', 'generation_failures', 'judge_errors')))
    return scored, finite_score(row.get('mean')) if full else None


def appearance_scores(report):
    from tools.evaluate_video_photographic_gpt61 import RUBRIC_SHA256, VERSION

    if (report.get('phase'),report.get('judge'),report.get('version'),report.get('rubric_sha256')) != (
            'finished',JUDGE,VERSION,RUBRIC_SHA256) or report.get('failures'):
        return {}
    if report.get('completed_calls') != report.get('expected_calls'):
        return {}
    scope = {value[0]:(key,value[1]) for key,value in BENCHMARKS.items()}
    models = set(ROSTER) | {'seedance25-v1','minimax-h3-local'}
    results = {}
    for row in report.get('cohorts',[]):
        if row['benchmark'] not in scope or row['model'] not in models:
            continue
        key, expected = scope[row['benchmark']]
        cases, ratings, unavailable = (row[k] for k in ('cases','ratings','no_evidence'))
        assert type(cases) is int and 1 <= cases <= expected and ratings+unavailable == 2*cases
        complete = cases == expected and ratings == 2*expected
        score = finite_score(row.get('mean')) if complete else None
        if score is not None and not 1 <= score <= 5:
            raise ValueError('Appearance mean is outside the frozen scale')
        entry = results.setdefault(row['model'],{})
        if key in entry:
            raise ValueError('Duplicate appearance cohort')
        entry[key] = {'score':score,'cases':cases,'expected':expected,'ratings':ratings,
                      'no_evidence':unavailable,'complete':complete}
    return results


def harness_progress(root):
    """Project only model labels, case counts, and launch state from private runs."""
    joined = {}
    live_source = read(root/'opencode_live_status.json')
    live = {}
    updated = live_source.get('updated_utc')
    updated = updated if isinstance(updated, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+', updated) else None
    for row in live_source.get('models', []):
        model, expected = row.get('model_key'), row.get('expected')
        if model not in ROSTER:
            continue
        if expected != BENCHMARKS['physics_iq'][1]:
            raise ValueError('Unexpected OpenCode live scope')
        counts = public_counts(row, expected)['counts']
        released = row.get('full_released') is True
        phase = row.get('phase')
        phase = phase if phase in ('full_running', 'full_complete', 'full_released', 'canary_running',
                                   'canary_waiting', 'canary_failed', 'canary_scoring', 'registered') else None
        state = ('full_complete' if counts['complete'] == expected else 'full_released') if released else (
            phase if phase in ('canary_running', 'canary_waiting', 'canary_failed', 'canary_scoring', 'registered')
            else 'canary_running' if counts['running'] else 'registered')
        live[model] = {'state': state, 'phase': phase, 'counts': counts, 'expected': expected,
                       'full_released': released, 'updated_utc': updated}
    registry = read(root/'dispatch.json').get('models', {})
    for model in ROSTER:
        if model not in registry:
            continue
        matrix = {}
        for harness in ('opencode','claude_code'):
            if harness == 'opencode' and model in live:
                matrix[harness] = live[model]
                continue
            entry = registry[model].get(harness)
            if not entry:
                continue
            expected = entry['registered_cases']
            if expected != BENCHMARKS['physics_iq'][1]:
                raise ValueError('Unexpected local native matrix scope')
            config_path = Path(entry['config']).resolve()
            if not config_path.is_relative_to(root.resolve()):
                raise ValueError('Unregistered native matrix configuration')
            config = read(config_path)
            runs = Path(config['runs_root']).resolve()
            if not runs.is_relative_to(root.resolve()):
                raise ValueError('Unregistered native matrix output directory')
            released = entry.get('full_supervisor_started') is True
            state = 'registered'
            counts = public_counts({}, expected)['counts']
            if released:
                snap = read(runs/'status.json')
                group = snap.get('groups', {}).get(f'{model}/{TRACK}')
                if group is not None and snap.get('cases') == expected and group.get('model_key') == model:
                    counts = public_counts(group, expected)['counts']
                    state = 'full_complete' if counts['complete'] == expected else 'full_released'
                else:
                    state = 'full_released'
            elif entry.get('acceptance_pid'):
                case = entry['canary_case']
                if not isinstance(case, str) or not case.isascii() or not case.isdecimal():
                    raise ValueError('Invalid native canary case identity')
                attempt = read(runs/model/TRACK/f'case_{case}_attempt_01/run_manifest.json')
                try:
                    cmdline = (Path('/proc')/str(int(entry['acceptance_pid']))/'cmdline').read_bytes()
                except OSError:
                    cmdline = b''
                active = str(config_path).encode() in cmdline.split(b'\0')
                state = {'failed':'canary_failed','complete':'canary_scoring'}.get(
                    attempt.get('status'),'canary_running' if active else 'canary_waiting')
            matrix[harness] = {'state':state,'counts':counts,'expected':expected,'full_released':released}
        joined[model] = matrix
    for model, entry in live.items():
        joined.setdefault(model, {})['opencode'] = entry
    return joined


def build(nightly, publication):
    cat = read(publication / 'catalogue.json')
    ev = read(publication / 'evaluation.json')
    cohorts = {(x.get('suite'), x.get('model_key')): x for x in ev.get('cohorts', []) if x.get('track') == TRACK}
    groups = {(x.get('suite'), x.get('model_key')): x for x in cat.get('groups', []) if x.get('track') == TRACK}
    evaluation = nightly / 'evaluation'
    rbench = read(evaluation / 'rbench_common_gpt61_live/execution.json')
    phygen = read(evaluation / 'phygen_gpt61_sol_three_stage/summary.json')
    phygen_deepseek = read(evaluation / 'phygen_gpt61_sol_deepseek/summary.json')
    photo = read(evaluation / 'photographic_gpt61_existing_main/execution.json')
    fvd_report = read(evaluation / 'physics_iq_fvd_official_take1/summary.json')
    pcve_fvd = {row['model']: row for row in read(evaluation / 'pcve_reused_official_fvd.json').get('verified_scores', [])}
    rconfigs = rbench_configs(nightly / 'rbench_full_dispatch')
    registry = read(nightly / 'gpt62_cohorts/dispatch_registry.json')['configured']
    gpt62 = {x['name']: load_config(Path(x['config'])) for x in registry if x['name'].endswith('_full')}
    rows = {}
    for model in ROSTER:
        cells = {}
        for key, (suite, expected, metric) in BENCHMARKS.items():
            if key == 'rbench':
                progress = count_config(*rconfigs[model], expected) if model in rconfigs else public_counts({}, expected)
                scored, score = rbench_score(rbench, model)
            else:
                config = gpt62.get(key + '_full') if model == 'gpt62-sol' else None
                progress = count_config(config, config.model(model), expected) if config else public_counts(groups.get((suite, model), {}), expected)
                record = cohorts.get((suite, model), {})
                if key == 'phygenbench':
                    scored, score = phygen_score(phygen, model)
                    if model == 'deepseek-v41' and not scored:
                        scored, score = phygen_score(phygen_deepseek, model)
                else:
                    score = legacy_score(record, expected, metric)
                    scored = expected if score is not None else 0
            if progress['counts']['complete'] < expected:
                score = None
            cells[key] = {**progress, 'expected': expected, 'scored': scored, 'score': score, 'metric': metric}
            if key == 'physics_iq' and fvd_report.get('full198') is True:
                detail = fvd_report.get('scores', {}).get(model, {})
                if detail.get('count') == expected:
                    cells[key]['fvd'] = finite_score(detail.get('fvd'))
            elif key == 'pcve' and score is not None:
                detail = pcve_fvd.get(model, {})
                reference = cohorts.get((suite, model), {}).get('score') or {}
                fvd = finite_score(reference.get('metrics', {}).get('FVD'))
                if detail.get('count') == expected and fvd is not None and detail.get('fvd') == fvd:
                    cells[key]['fvd'] = fvd
        rows[model] = cells
    from tools.publication_coverage import coverage
    indexes = {group['path']: read(publication/group['path']) for group in cat.get('groups', [])}
    upload_coverage = coverage(cat, indexes, read(publication.parent.parent/'state/hf_incremental.json'))
    plan = read(publication / 'experiment_plan.json')
    baseline_keys = {'seedance25': 'seedance25-v1', 'minimax-h3': 'minimax-h3-local'}
    references = {}
    for row in plan.get('video_baselines', []):
        key = row.get('key')
        if key not in baseline_keys:
            continue
        scores = {}
        for benchmark in ('physics_iq', 'pcve'):
            item = row.get('scores', {}).get(benchmark, {})
            if item.get('kind') == 'published_reference':
                scores[benchmark] = finite_score(item.get('value'))
        _, scores['phygenbench'] = phygen_score(phygen, baseline_keys[key])
        references[key] = scores
    baseline_report = read(evaluation/'physics_iq_local_baselines/summary.json')
    baseline_rechecks = [{k: row.get(k) for k in ('cohort','model','status','partial_done','expected','source_count','selected_count','failed','missing_ids','official_full_score')}
                         for row in baseline_report.get('cohorts', [])]
    native = native_main_smoke(evaluation/'astra62_native_main_smoke.json', evaluation/'astra62_native_smoke_source.json')
    if native:
        rows['gpt62-astra-native'] = native_experiment_cells(native)
    return {'protocol': 'pixelworld-main-suite-20261124', 'updated_utc': utc_now(), 'models': rows,
            'references': references,
            'harness_progress': harness_progress(nightly/'controlled_harness_matrix'),
            'photographic_progress': {key: photo.get(key) for key in ('completed_calls', 'expected_calls')},
            'photographic_scores': appearance_scores(photo),
            'photographic_judge': JUDGE, 'publication_coverage': upload_coverage,
            'baseline_rechecks': baseline_rechecks,
            'native_main_smoke': native}


LABELS = {
    'gpt62-sol': 'GPT-6.2-Sol', 'gpt61-sol': 'GPT-6.1-Sol', 'gpt6-astra': 'GPT-6-Astra',
    'gpt56-sol': 'GPT-5.6-Sol', 'gpt56-terra': 'GPT-5.6-Terra', 'gpt56-luna': 'GPT-5.6-Luna',
    'deepseek-v41': 'DeepSeek-V4.1-Flash', 'seed21-pro': 'Seed-2.1-Pro-0915',
    'gpt62-astra-native': 'GPT-6.2-Astra',
}
HEADER = {
    'physics_iq': ('Physics-IQ Verified', 'I2V · 198 cases', 'physics_iq_baseline.html'),
    'rbench': ('RBench Common Manipulation', 'I2V · 50 cases', 'rbench_common_manipulation.html'),
    'phygenbench': ('PhyGenBench', 'T2V · 160 cases', 'phygenbench.html'),
    'pcve': ('PCVE-RigidBench', 'V2V editing · 129 cases', 'pcve_rigid.html'),
}


def render(status):
    def display_score(key, score):
        digits = {'physics_iq': 2, 'pcve': 3, 'rbench': 3, 'phygenbench': 4}[key]
        return f'{score:.{digits}f}'

    def viewer_href(key, model):
        if model == 'gpt62-astra-native':
            return f'videos.html?benchmark={key}&amp;model={model}'
        suite = BENCHMARKS[key][0]
        if key == 'rbench': suite += '_' + model
        elif model == 'gpt62-sol': suite += '_gpt62'
        index = quote(f'{suite}/{model}/{TRACK}/index.json', safe='')
        return f'videos.html?index={index}'

    def cell(row, key, model):
        count, total = row['counts'], row['expected']
        _, _, href = HEADER[key]
        progress = f"<b>{count['complete']} / {total}</b>"
        viewer = viewer_href(key, model)
        detail = f'{href}?model={quote(model)}' if href else viewer
        progress = f'<a href="{detail}">{progress}</a>'
        if row['score'] is None:
            evaluation = f"评分 {row['scored']} / {total}"
        else:
            evaluation = f"<b>{escape(row['metric'])} {display_score(key, row['score'])}</b>"
        recent = row.get('last_delivery_utc')
        last = (f'<time datetime="{escape(recent)}">{escape(recent)}</time>' if recent else
                '交付时间待同步' if count['complete'] else '尚无交付')
        pending_label = '待补齐（额度审核）' if model == 'gpt62-astra-native' else '待生成'
        return (f'<td>{progress}<div class="progress"><i style="width:{100*count["complete"]/total:.1f}%"></i></div>'
                f'<div class="small muted">{count["running"]} 运行 · {count["pending"]} {pending_label} · {count["exhausted"]} 终止</div>'
                f'<div>{evaluation}</div><div class="small muted">最近交付：{last}</div>'
                f'<a class="small" href="{viewer}">视频与 Trace →</a></td>')

    def baseline_cell(scores, key):
        value = scores.get(key)
        if value is None:
            return '<td>—</td>'
        origin = 'GPT-6.1-Sol 重评' if key == 'phygenbench' else '论文 / 榜单参考'
        return f'<td><b>{display_score(key, value)}</b><br><span class="small muted">{origin}</span></td>'

    heads = ''.join(f'<th><a href="{href or "videos.html?benchmark=" + key}">{label}</a><br>'
                    f'<span class="small muted">{spec}</span></th>' for key, (label, spec, href) in HEADER.items())
    baseline_labels = {'seedance25': 'Seedance 2.5', 'minimax-h3': 'MiniMax H3'}
    rows = ''.join(f'<tr class="baseline"><th>{baseline_labels[model]}<br><span class="small muted">VideoGen baseline</span></th>' +
                   ''.join(baseline_cell(scores, k) for k in HEADER) + '</tr>'
                   for model, scores in status.get('references', {}).items())
    model_order = sorted(status['models'], key=lambda model: model != 'gpt62-astra-native')
    rows += ''.join((f'<tr class="native-row"><th>{LABELS[model]}<br><span class="small muted">Codex 0.172.0 · high · default</span></th>'
                     if model == 'gpt62-astra-native' else f'<tr><th>{LABELS[model]}</th>')
                    + ''.join(cell(status['models'][model][k], k, model) for k in HEADER) + '</tr>'
                    for model in model_order)
    fvd_rows = ''.join(f'<tr><th>{LABELS[model]}</th>' + ''.join(
        '<td>—</td>' if cells[k].get('fvd') is None else f'<td>{cells[k]["fvd"]:.2f}</td>'
        for k in ('physics_iq', 'pcve')) + '</tr>' for model, cells in status['models'].items()
        if any(cells[k].get('fvd') is not None for k in ('physics_iq', 'pcve')))
    def harness_cell(entry):
        if not entry:
            return '<td>—</td>'
        counts = entry['counts']
        if entry['full_released']:
            return (f"<td><b>{counts['complete']} / {entry['expected']}</b><br><span class='small muted'>"
                    f"{counts['running']} 运行 · {counts['pending']} 待生成 · {counts['exhausted']} 终止</span></td>")
        phase = {'canary_running':'验收生成中','canary_scoring':'验收评分 / 归档','canary_failed':'验收未通过',
                 'canary_waiting':'验收待核查','registered':'已登记'}[entry['state']]
        return f'<td>{phase}<br><span class="small muted">{counts["running"]} 运行 · 未发全量</span></td>'

    harness = status.get('harness_progress', {})
    harness_updated = max((entry.get('updated_utc') for entries in harness.values() for entry in entries.values()
                           if entry.get('updated_utc')), default=None)
    freshness = f'<p class="small muted">OpenCode 更新：<time datetime="{escape(harness_updated)}">{escape(harness_updated)}</time></p>' if harness_updated else ''
    harness_rows = ''.join(f"<tr><th>{LABELS[model]}</th><td><b>{status['models'][model]['physics_iq']['counts']['complete']} / 198</b></td>" +
                 ''.join(harness_cell(entries.get(k)) for k in ('opencode','claude_code'))+'</tr>'
                 for model,entries in harness.items())
    harness_section = (f'<details id="harness-progress"><summary>Physics-IQ · Harness 对照进度</summary>'
        '<p class="small muted">Vision · 同模型与输入 · 720p / 24 FPS。只显示有效交付，验收样本不是全量分数。</p>'
        f'{freshness}'
        '<div class="table"><table><thead><tr><th>模型</th><th>Codex 基准</th><th>OpenCode</th><th>Claude Code</th>'
        f'</tr></thead><tbody>{harness_rows}</tbody></table></div></details>' if harness_rows else '')
    def photo_cell(row):
        if row is None:
            return '<td>—</td>'
        score = row['score']
        value = '—' if score is None else f'<b>{score:.2f}</b>'
        label = f"{row['cases']} / {row['expected']} 题"
        if row['no_evidence']:
            label += f" · {row['no_evidence']} 次无法评分"
        return f'<td>{value}<br><span class="small muted">{label}</span></td>'

    labels = {**LABELS,'seedance25-v1':'Seedance 2.5','minimax-h3-local':'MiniMax H3 · 本地'}
    photos = status.get('photographic_scores', {})
    photo_rows = ''.join(f'<tr><th>{labels[model]}</th>' + ''.join(photo_cell(cells.get(k)) for k in HEADER) + '</tr>'
                         for model,cells in photos.items())
    photo_table = ('<h3>主观画质（1–5，越高越好）</h3><div class="table"><table><thead><tr><th>模型</th>' + heads +
                   f'</tr></thead><tbody>{photo_rows}</tbody></table></div>'
                   '<p class="small muted">仅在整个 benchmark 的每题两种播放顺序均可评分时显示均值。'
                   'RBench 当前只有 1 题验收；各 benchmark 内容和条件不同，不据此推断输入条件的因果效应。</p>'
                   if photo_rows else '')
    photo = status['photographic_progress']
    baseline_rows = ''.join(f'<tr><th>{escape(str(row.get("model","")))}</th><td>{escape(str(row.get("cohort","")))}</td>'
                            f'<td>{row.get("source_count") or 0} / {row.get("expected") or 198}</td>'
                            f'<td>{row.get("partial_done") or 0} / {row.get("selected_count") or 0} · {escape(str(row.get("status","")))}</td></tr>'
                            for row in status.get('baseline_rechecks', []))
    baseline_section = ('<details id="baseline-rechecks"><summary>Physics-IQ · 本地 VideoGen 复评</summary>'
                        '<div class="table"><table><tr><th>模型</th><th>组</th><th>本地视频</th><th>本组评分进度</th></tr>'+baseline_rows+'</table></div>'
                        '<p class="small muted">不完整集合只作子集复评，不覆盖主表中的已发表参考分。Seedance 本地片段保留含首帧的前 120 帧，与已发表四次运行的帧对齐不同。</p></details>'
                        if baseline_rows else '')
    upload = status.get('publication_coverage', {}).get('totals', {})
    upload_section = (f'<details id="publication-status"><summary>HF 视频与 Trace 同步进度</summary>'
                      f'<p>已关闭运行记录 {upload.get("closed_records",0)} · 详情已上传 {upload.get("details_uploaded",0)} · '
                      f'Trace 已上传 {upload.get("traces_uploaded",0)} · 待上传 Trace {upload.get("traces_pending",0)}</p>'
                      '<p class="small muted">包括失败与 infra 记录；统计匹配当前详情哈希的上传回执。生成完成和上传完成分别记录。</p>'
                      '<a href="https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/metadata/publication_coverage.json">各模型覆盖记录 →</a></details>'
                      if upload else '')
    smoke = status.get('native_main_smoke', {})
    additions = smoke.get('additional_samples', [])
    def smoke_number(value, digits=None):
        value = finite_score(value)
        if value is None:
            return '待记录'
        return f'{value:,.0f}' if digits is None else f'{value:,.{digits}f}'

    smoke_states = {'pending': '待运行', 'waiting': '等待', 'generating': '生成中', 'running': '运行中',
                    'validating': '验证中', 'complete': '完成', 'finished': '完成', 'failed': '失败',
                    'blocked': '待恢复', 'skipped': '未运行'}
    smoke_samples = {(row.get('round', 1), row['benchmark']): row for row in smoke.get('samples', [])}
    smoke_rounds = smoke.get('expected_rounds', 1)
    if type(smoke_rounds) is not int or smoke_rounds not in (1, 2):
        raise ValueError('Native smoke permits at most two rounds')
    smoke_rows = ''
    for round_no, key in ((round_no, key) for round_no in range(1, smoke_rounds+1) for key in HEADER):
        label = HEADER[key][0]
        row = smoke_samples.get((round_no, key), {})
        usage = row.get('token_usage') or {}
        shared_quota = row.get('quota_attribution') == 'shared_parallel_window'
        suite = key + '_native_astra62_smoke' + ('_round2' if round_no == 2 else '')
        viewer = 'videos.html?index=' + quote(f'{suite}/gpt62-astra-native/{TRACK}/index.json', safe='')
        link = f'<br><a class="small" href="{viewer}">运行 / 视频 / Trace →</a>' if row.get('case') else ''
        smoke_rows += (f'<tr><th>第 {round_no} 轮 · {label}<br><span class="small muted">{escape(row.get("case") or "待记录")}</span>{link}</th>'
            f'<td>{smoke_states.get(row.get("state"), "待运行")}</td>'
            + ''.join(f'<td>{smoke_number(usage.get(field))}</td>' for field in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
            + f'<td>{smoke_number(row.get("duration_seconds"), 1)}{" 秒" if row.get("duration_seconds") is not None else ""}</td>'
            + f'<td>{"整轮看汇总" if shared_quota else smoke_number(row.get("quota_weekly_consumed"), 2)}</td>'
            + ''.join(f'<td>{"整轮看汇总" if shared_quota and field == "credits_consumed" else "接口未提供" if field != "credits_consumed" and row.get("state") in ("complete","failed") and row.get(field) is None else smoke_number(row.get(field), 4)}</td>' for field in ('credits_consumed', 'credits_estimated', 'usd_estimated'))
            + '</tr>')
    round_summary = ''.join(
        f'<p><b>第 {row["round"]} 轮 · {"并行阶段合计（含追加样本重叠）" if additions and row.get("execution_mode") == "parallel" else {"serial": "串行", "parallel": "并行"}.get(row.get("execution_mode"), "执行方式待记录")}</b>'
        f' · {"阶段" if additions and row.get("execution_mode") == "parallel" else "整轮"}周额度变化 {smoke_number(row.get("weekly_change"), 2)} 百分点'
        f' · Credits 余额差 {smoke_number(row.get("credits_change"), 4)}</p>'
        for row in smoke.get('round_summaries', []))
    round_summary = (f'<div id="native-smoke-round-summaries">{round_summary}'
        '<p class="small muted">并行样本共享整轮额度窗口，额度变化不分摊到单个样本；各自 Tokens 与耗时分别记录。</p></div>'
        if round_summary else '')
    cumulative = smoke.get('cumulative_quota', {})
    cumulative_section = (f'<p id="native-smoke-cumulative-quota"><b>累计额度 · 最近读取</b>'
        f' · 周剩余 {smoke_number(cumulative.get("remaining_before"))}% → {smoke_number(cumulative.get("remaining_latest"))}%'
        f' · 观测变化 {smoke_number(cumulative.get("observed_change_pp"), 2)} 百分点'
        f' · Credits 余额差 {smoke_number(cumulative.get("credits_change"), 4)}</p>'
        '<p class="small muted">累计变化包含当前运行中的并行与追加样本，不能归因到单个样本或第二轮四个样本。</p>'
        if cumulative else '')
    additional_rows = ''
    for row in additions:
        case = row['case']
        usage = row.get('token_usage') or {}
        suite = f'physics_iq_native_astra62_extra_{case}'
        viewer = 'videos.html?index=' + quote(f'{suite}/gpt62-astra-native/{TRACK}/index.json', safe='')
        additional_rows += (f'<tr><th>Physics-IQ 追加 {escape(case)}<br><a class="small" href="{viewer}">运行 / 视频 / Trace →</a></th>'
            f'<td>{smoke_states.get(row.get("state"), "待运行")}</td>'
            + ''.join(f'<td>{smoke_number(usage.get(field))}</td>' for field in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
            + f'<td>{smoke_number(row.get("duration_seconds"), 1)}{" 秒" if row.get("duration_seconds") is not None else ""}</td></tr>')
    additional_section = ('<div id="native-smoke-additional"><h3>追加样本</h3>'
        '<div class="table"><table><thead><tr><th>样本</th><th>状态</th><th>Input tokens</th><th>Cached input</th>'
        f'<th>Output tokens</th><th>耗时</th></tr></thead><tbody>{additional_rows}</tbody></table></div>'
        '<p class="small muted">追加样本单列，不计作第三轮；与并行阶段共享额度窗口，额度变化看累计汇总。</p></div>'
        if additions else '')
    smoke_section = ('<section id="native-main-smoke"><h2>GPT-6.2-Astra · 主实验配置 smoke</h2>'
        f'<p class="muted">{escape(smoke.get("cli_version") or "CLI 版本待记录")} · reasoning {escape(smoke.get("reasoning_effort") or "待记录")} · '
        f'service tier {escape(smoke.get("service_tier") or "待记录")}</p>'
        f'<p class="small muted">已安排 {smoke_rounds} 轮，每轮每个 benchmark 一个标准样本，最多两轮八个样本。'
        + (f'另有 {len(additions)} 个追加样本，单独列示。' if additions else '') +
        '原生 Codex 0.172.0，独立于主表冻结的 0.169.0 队列，不代表全量结果。</p>'
        f'{round_summary}'
        f'{cumulative_section}'
        '<div class="table"><table><thead><tr><th>轮次 / Benchmark / Case</th><th>状态</th><th>Input tokens</th>'
        '<th>Cached input</th><th>Output tokens</th><th>耗时</th><th>周额度 · 实测消耗（百分点）</th><th>Credits · 余额差</th>'
        '<th>Credits · 估算</th><th>USD · 估算</th></tr></thead>'
        f'<tbody>{smoke_rows}</tbody></table></div>'
        f'{additional_section}'
        '<p class="small muted">Tokens 来自实际运行记录；周额度为同一重置窗口内的使用率变化，实测 Credits 为余额变化，估算值单独列示。'
        '周额度显示整数百分比，0 变化不代表零计算消耗；接口未返回估算值时不按零计。Cached input 已包含在 Input 中。</p></section>' if smoke else '')
    study = smoke.get('quota_study', {})
    study_rows = ''
    for row in study.get('samples', []):
        if row.get('state') not in NATIVE_RUN_STATES:
            continue
        key, case = row['benchmark'], row['case']
        usage = row.get('token_usage') or {}
        suite = key + '_native_astra62_quota5'
        viewer = 'videos.html?index=' + quote(f'{suite}/gpt62-astra-native/{TRACK}/index.json', safe='')
        study_rows += (f'<tr><th>{HEADER[key][0]} · {escape(case)}<br>'
            f'<a class="small" href="{viewer}">运行 / 视频 / Trace →</a></th>'
            f'<td>{smoke_states.get(row.get("state"), "待运行")}</td>'
            + ''.join(f'<td>{smoke_number(usage.get(field))}</td>' for field in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
            + f'<td>{smoke_number(row.get("duration_seconds"), 1)}{" 秒" if row.get("duration_seconds") is not None else ""}</td></tr>')
    totals = smoke.get('cumulative_totals', {})
    tokens = totals.get('token_usage') or {}
    baseline, target = study.get('baseline_remaining_percent'), study.get('target_remaining_percent')
    target_delta = baseline-target if baseline is not None and target is not None else None
    study_statuses = {**smoke_states, 'draining': '等待运行中样本结束', 'stopping': '停止发新样本',
                      'stopped': '已停止', 'paused': '暂停', 'target_reached': '目标已达'}
    stop_labels = {'target_reached': '目标已达', 'reserve_for_inflight': '为运行中样本预留额度',
                   'quota_unavailable': '额度暂不可读', 'quota_window_changed': '额度窗口已变化',
                   'memory_pressure': '内存不足，等待释放', 'all_cases_processed': '样本已处理完', 'interrupted': '已中断'}
    study_section = ('<section id="native-quota-study"><h2>GPT-6.2-Astra · 5 个百分点额度研究</h2>'
        f'<p class="muted">{escape(study.get("cli_version") or "CLI 版本待记录")} · reasoning {escape(study.get("reasoning_effort") or "待记录")} · '
        f'service tier {escape(study.get("service_tier") or "待记录")} · 并行</p>'
        f'<p><b>周剩余目标 {smoke_number(baseline)}% → {smoke_number(target)}%</b>'
        f' · 当前 {smoke_number(study.get("current_remaining_percent"))}%'
        f' · 已观测 {smoke_number(study.get("consumed_percentage_points"), 2)} / {smoke_number(target_delta, 2)} 百分点</p>'
        f'<p>状态：{study_statuses.get(study.get("status"), "待记录")} · 并发上限 {smoke_number(study.get("max_concurrency"))}'
        f' · 当前运行 {smoke_number(study.get("current_active"))} · 本阶段完成 {smoke_number(study.get("study_completed"))}'
        f' · 失败 {smoke_number(study.get("failed"))}</p>'
        f'<p>累计完成 <b>{smoke_number(totals.get("completed"))}</b> / 已登记 {smoke_number(totals.get("registered_samples"))}'
        f'（含前期 {smoke_number(study.get("baseline_samples"))} 条）</p>'
        f'<p>累计已记录 Tokens：Input {smoke_number(tokens.get("input_tokens"))}'
        f' · Cached input {smoke_number(tokens.get("cached_input_tokens"))} · Output {smoke_number(tokens.get("output_tokens"))}'
        f' · 用量记录 {smoke_number(totals.get("samples_with_usage"))} / {smoke_number(totals.get("registered_samples"))}</p>'
        + (f'<p>停止 / 等待原因：{stop_labels[study["stop_reason"]]}</p>' if study.get('stop_reason') in stop_labels else '') +
        '<div class="table"><table><thead><tr><th>Benchmark / Case</th><th>状态</th><th>Input tokens</th>'
        f'<th>Cached input</th><th>Output tokens</th><th>耗时</th></tr></thead><tbody>{study_rows}</tbody></table></div>'
        '<p class="small muted">目标从原始周剩余额度累计计算，包含前期九条。Tokens 合计仅包含已记录用量，缺失值不按零计；'
        'Cached input 已包含在 Input 中。并行样本共享额度窗口，不向单条分配额度变化；与主表全量结果分开。</p></section>'
        if study else '')
    if smoke_section:
        smoke_section = '<details id="native-history"><summary>原生 Astra · 前期运行与额度记录</summary>'+smoke_section+'</details>'
    if study_section:
        study_section = (f'<details id="native-budget"><summary>原生 Astra · 额度研究 · 当前周剩余 {smoke_number(study.get("current_remaining_percent"))}%'
                         f' · 本阶段完成 {smoke_number(study.get("study_completed"))}</summary>'+study_section+'</details>')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pixelworld-updated" content="{status['updated_utc']}"><meta name="pixelworld-protocol" content="pixelworld-main-suite-20261124">
<title>PixelWorld · 主实验</title><link rel="stylesheet" href="formal.css"><style>.native-row th,.native-row td{{background:#f1f6ff}}.native-row th{{color:#24538c}}details summary{{font-size:14px}}table td{{min-width:180px}}table th:first-child{{min-width:180px}}</style><body><header><nav><strong><a href="index.html">PixelWorld</a></strong>
<span><a href="analyses.html">分析进度</a><a href="cost_effects.html">成本与效果</a><a href="pixelworld-index.html">历史总览</a>
<a href="robojudge.html">RoboJudge · 历史</a><a href="https://huggingface.co/datasets/HuggingFriends/PixelWorld">视频与 Trace</a>
<a href="https://github.com/SiyuanMaCS/PixelWorld">GitHub</a></span></nav></header>
<main><p class="eyebrow">CODING AGENTS AS PIXEL WORLD MODELS</p><h1>主实验</h1>
<p class="muted">Vision · 720p / 24 FPS · 数据快照 <time datetime="{escape(status['updated_utc'])}">{escape(status['updated_utc'])}</time></p>
<div class="table"><table><thead><tr><th>模型</th>{heads}</tr></thead><tbody>{rows}</tbody></table></div>
<p class="small muted">只有评完全部样本才显示总分。RBench 只生成一个候选，使用 GPT-6.1-Sol 的官方 GPT 分支适配，不是 GPT + Qwen 联合榜分。
PhyGenBench 为 GPT-6.1-Sol 三阶段 MCQ 适配，不与原评测器分数混排。PCVE 采用 Full 129；PES 校准与论文参照见详情页。</p>
<p class="small muted">原生 GPT-6.2-Astra 单独标记 Codex 0.172.0 / high / default；其余主队列冻结为 Codex 0.169.0。原生进度按跨批次去重样本计数，未完成官方全套评测时不显示总分。待补齐不代表已发任务，继续生成需在已授权额度内。</p>
{harness_section}
{baseline_section}
{upload_section}
{smoke_section}
{study_section}
<h2>视频质量 · 独立于物理分</h2><p>GPT-6.1-Sol 画质判分进度：{photo.get('completed_calls') or 0} / {photo.get('expected_calls') or 0} 次调用。</p>
{photo_table}
<h3>FVD（越低越好）</h3>
<div class="table"><table><thead><tr><th>模型</th><th>Physics-IQ FVD ↓</th><th>PCVE FVD ↓</th></tr></thead><tbody>{fvd_rows}</tbody></table></div>
<p class="small muted">FVD 使用各 benchmark 对应的参考集，不能跨 benchmark 比较。</p></main>
<script>
const clock = new Intl.RelativeTimeFormat('zh-CN', {{numeric: 'always'}});
function refreshAges() {{
  for (const time of document.querySelectorAll('time[datetime]')) {{
    const ms = Date.parse(time.dateTime);
    if (!Number.isFinite(ms)) continue;
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    const [divisor, unit] = sec < 60 ? [1, 'second'] : sec < 3600 ? [60, 'minute'] :
      sec < 86400 ? [3600, 'hour'] : [86400, 'day'];
    time.title = time.dateTime;
    time.textContent = clock.format(-Math.floor(sec / divisor), unit);
  }}
}}
refreshAges();
setInterval(refreshAges, 15000);
let published = document.querySelector('meta[name="pixelworld-updated"]').content;
let polling = false;
const datasetBase = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/';
async function checkSnapshot() {{
  if (polling) return;
  polling = true;
  try {{
    const query = '?_=' + Date.now();
    const reply = await fetch(datasetBase + 'metadata/main_suite_status.json' + query, {{cache: 'no-store'}});
    if (!reply.ok) throw new Error('snapshot unavailable');
    const data = await reply.json();
    if (data.protocol !== 'pixelworld-main-suite-20261124' || !data.updated_utc) throw new Error('unexpected snapshot');
    if (data.updated_utc !== published) {{
      const page = await fetch(datasetBase + 'dashboard/index.html' + query, {{cache: 'no-store'}});
      if (!page.ok) throw new Error('dashboard unavailable');
      const parsed = new DOMParser().parseFromString(await page.text(), 'text/html');
      if (parsed.querySelector('meta[name="pixelworld-updated"]')?.content !== data.updated_utc) throw new Error('snapshot still propagating');
      const next = parsed.querySelector('main');
      if (!next) throw new Error('dashboard incomplete');
      document.querySelector('main').replaceWith(next);
      published = data.updated_utc;
      document.querySelector('meta[name="pixelworld-updated"]').content = published;
      refreshAges();
    }}
    document.documentElement.dataset.sync = 'ok';
  }} catch (error) {{ document.documentElement.dataset.sync = 'retrying'; }}
  finally {{ polling = false; }}
}}
checkSnapshot();
setInterval(checkSnapshot, 60000);
document.addEventListener('visibilitychange', () => {{ if (!document.hidden) checkSnapshot(); }});
</script></body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nightly', type=Path, required=True)
    p.add_argument('--publication', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--html', type=Path, help='Optional standalone preview next to formal.css')
    a = p.parse_args()
    status = build(a.nightly, a.publication)
    atomic_json(a.output, status)
    if a.html:
        a.html.write_text(render(status), encoding='utf-8')


if __name__ == '__main__':
    main()
