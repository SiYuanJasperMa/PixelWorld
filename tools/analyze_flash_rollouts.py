"""Measure Flash development arms from recorded model usage and actual tool calls."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import statistics

import numpy as np

ARMS = {
    'reference': ('baseline', 'Baseline'),
    'guidance': ('costprofile', 'Five-line guidance'),
    'tools': ('flash_tools', 'Summary + run/check'),
    'combined': ('flash_combined', 'Guidance + helpers'),
    'lean': ('flash_lean', 'Two-line guidance + helpers'),
    'guidance_lean': ('flash_guidance_lean', 'Two-line guidance'),
    'guidance_lean_tmp': ('flash_guidance_lean_tmp', 'Two lines + scratch path'),
    'flash_final': ('flash_final', 'Five lines + scratch path'),
    'aggressive_caps': ('flash_aggressive_caps', 'Four commands / one candidate'),
    'oneshot': ('flash_oneshot', 'One-shot guidance'),
    'api': ('flash_api', 'Compact save API'),
    'api_receipt': ('flash_api_receipt', 'Compact API + write receipt'),
}


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def mean(values):
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return statistics.mean(values) if values else None


def recorded_trace(path):
    calls = Counter()
    images = Counter()
    reads = Counter()
    text_bytes = 0
    usages = []
    seen_usage = set()
    seen_calls = set()
    image_call_ids = set()
    image_results = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(errors='replace').splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        value = record.get('payload') or {}
        if record.get('type') == 'event_msg' and value.get('type') == 'token_count':
            info = value.get('info') or {}
            cumulative = info.get('total_token_usage') or {}
            key = tuple(cumulative.get(k) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens'))
            last = info.get('last_token_usage') or {}
            if last.get('input_tokens') and key not in seen_usage:
                seen_usage.add(key)
                usages.append(last)
        if record.get('type') != 'response_item':
            continue
        if value.get('type') in ('function_call','custom_tool_call') and value.get('call_id') not in seen_calls:
            seen_calls.add(value.get('call_id'))
            name = value.get('name') or 'unknown'
            calls[name] += 1
            try:
                arguments = json.loads(value.get('arguments') or '{}')
            except ValueError:
                arguments = {}
            if name == 'view_image':
                image_call_ids.add(value.get('call_id'))
                target = Path(arguments.get('path') or '').name
                images['input' if target == 'first_frame.png' else 'other'] += 1
            command = arguments.get('cmd') or ''
            if re.search(r'\b(?:cat|sed|head|tail|open)\b|read_text', command):
                for filename in ('artifact_io.py', 'player_template.html', 'INPUT_SUMMARY.json',
                                 'pixelworld_run.py', 'pixelworld_check.py'):
                    if filename in command:
                        reads[filename] += 1
        if value.get('type') == 'function_call_output' and value.get('call_id') in image_call_ids:
            result = value.get('output')
            if isinstance(result, list) and any(block.get('type') in ('input_image', 'image', 'image_url') for block in result):
                image_results['returned_image_messages'] += 1
            elif 'digest mismatch' in str(result):
                image_results['helper_digest_failures'] += 1
            else:
                image_results['other_or_unresolved'] += 1
        if value.get('type') in ('function_call_output','custom_tool_call_output') and isinstance(value.get('output'), str):
            text_bytes += len(value['output'].encode())
    total_input = sum(v.get('input_tokens', 0) for v in usages)
    initial = usages[0]['input_tokens'] if usages else None
    return {'trace_schema_version':2,'calls': dict(calls), 'image_calls': dict(images), 'image_results': dict(image_results), 'source_reads': dict(reads),
            'tool_text_bytes': text_bytes, 'model_requests': len(usages),
            'rollout_input_tokens': total_input,
            'rollout_output_tokens': sum(v.get('output_tokens', 0) for v in usages),
            'initial_request_input_tokens': initial,
            'history_amplification_proxy': total_input / (initial * len(usages)) if initial else None}


def paired_quality(reference, current, scenario, seed=6141):
    shared = sorted(set(reference) & set(current))
    if not shared:
        return None
    groups = {}
    for key in shared:
        groups.setdefault(scenario.get(key, key), []).append(key)
    labels = sorted(groups)
    rng = np.random.default_rng(seed)
    result = {'paired_cases': len(shared), 'scenario_groups': len(labels),
              'bootstrap_unit': 'scenario' if scenario else 'case',
              'relative_loss_margin': 0.05, 'confirmatory': False}
    for metric in ('v1', 'v2'):
        delta = {k: current[k][metric] - reference[k][metric] for k in shared}
        samples = []
        for indices in rng.integers(0, len(labels), size=(5000, len(labels))):
            keys = [key for index in indices for key in groups[labels[index]]]
            samples.append(sum(delta[key] for key in keys) / len(keys))
        ref_mean = mean([reference[k][metric] for k in shared])
        # A one-scenario smoke cannot estimate sampling uncertainty.
        interval = np.quantile(samples, [.025, .975]).tolist() if len(labels) > 1 else None
        result[metric] = {'mean_delta': mean(list(delta.values())), 'reference_mean': ref_mean,
            'relative_mean_delta': mean(list(delta.values())) / ref_mean if ref_mean else None,
            'ci95': interval, 'wins': sum(v > 0 for v in delta.values()),
            'losses': sum(v < 0 for v in delta.values()),
            'development_interval_within_margin': bool(interval and len(labels) >= 20
                and ref_mean and interval[0] >= -0.05 * ref_mean)}
    return result


def build(root, output, descriptions=None, cache_path=None, quality_checkpoint=None):
    cache = read(cache_path) if cache_path else {}
    quality = read(quality_checkpoint or root / 'quality/flash_quality_checkpoint_v2.json')
    if quality_checkpoint is None and quality.get('mask_assignment') != 'per-case-v2':
        quality = read(root / 'quality/flash_quality.json')
    scored = {}
    if quality.get('mask_assignment') == 'per-case-v2':
        for row in quality.get('per_case', []):
            scored.setdefault(row['arm'], {})[row['case']] = row
    scenario = {}
    if descriptions:
        with descriptions.open() as stream:
            for row in csv.DictReader(stream):
                name = row['generated_video_name']
                scenario[name[:4]] = name.split('_', 2)[-1]
    experiment = read(root / 'experiment_manifest.json')
    definitions = ({'baseline': ('generations/baseline', 'Baseline'),
                    'api': ('generations/api', 'Compact save API'),
                    'receipt': ('generations/receipt', 'Compact API + write receipt'),
                    'strict': ('generations/strict', 'Four-call guidance')} if experiment else ARMS)
    if experiment and 'replay' in experiment.get('arms',[]):
        definitions['replay']=('generations/replay','Replay pilot · task-read regression')
    if experiment and 'taskpack' in experiment.get('arms',[]):
        definitions['taskpack']=('generations/taskpack','Exact initial task pack')
    for key,label in [('replay_keepcalls','Retained calls · replay'),('core_tools','Core task tools')]:
        if experiment and key in experiment.get('arms',[]):definitions[key]=('generations/'+key,label)
    reference_key = 'baseline' if experiment else 'reference'
    arms = []
    for key, (folder, label) in definitions.items():
        manifests = list((root / folder / 'gpt56-luna/vision-agent').glob('*/run_manifest.json'))
        rows = []
        statuses = Counter()
        valid_rgb = 0
        valid_protocol = 0
        infra = 0
        closed_noninfra = 0
        trace_rows = []
        for path in manifests:
            record = read(path)
            status = record.get('status') or 'unknown'
            statuses[status] += 1
            validation = record.get('validation') or {}
            artifact_ok = (validation.get('frames') == 120 and validation.get('png_tensor_exact')
                and validation.get('frame0_exact') in (True, None) and not validation.get('missing_required'))
            valid_rgb += bool(artifact_ok)
            valid_protocol += bool(validation.get('pass'))
            interrupted = bool(validation.get('infra_error'))
            infra += interrupted
            if status == 'running':
                continue
            closed_noninfra += int(not interrupted)
            usage = record.get('token_usage') or {}
            if (not interrupted and usage.get('status') == 'recorded'
                    and isinstance(usage.get('input_tokens'), int)
                    and isinstance(usage.get('output_tokens'), int)):
                cached = min(usage['input_tokens'], max(0, usage.get('cached_input_tokens') or 0))
                rows.append({'input': usage['input_tokens'], 'output': usage['output_tokens'],
                    'protocol_delivery': status=='complete' and bool(validation.get('pass')),
                    'cached': cached, 'reasoning': usage.get('reasoning_output_tokens'),
                    'duration_seconds': record.get('duration_seconds'),
                    'cost_measured_cache': (usage['input_tokens']-cached)*10/1e6 + cached/1e6 + usage['output_tokens']*50/1e6,
                    'cost_normalized70': usage['input_tokens']*3.7/1e6 + usage['output_tokens']*50/1e6})
            trace = path.parent / 'logs/full_rollout.jsonl'
            if trace.is_file():
                cache_key = folder + '/' + path.parent.name
                stamp = [path.stat().st_mtime_ns, trace.stat().st_mtime_ns, trace.stat().st_size]
                saved = cache.get(cache_key)
                if saved is None or saved.get('stamp') != stamp or saved.get('data',{}).get('trace_schema_version')!=2:
                    saved = {'stamp': stamp, 'data': recorded_trace(trace)}
                    cache[cache_key] = saved
                trace_rows.append(saved['data'])
        current = scored.get(key, {})
        measured_deliveries=sum(r['protocol_delivery'] for r in rows)
        cost_complete=len(rows)==closed_noninfra
        expected_cases=len(experiment.get('arm_case_ids',{}).get(key,experiment.get('case_ids',[]))) if experiment else 20
        entry = {'excluded_from_acceptance': key=='replay' and bool(experiment and experiment.get('replay_status','').startswith('Initial pilot')), 'key': key, 'label': label, 'model': 'GPT-5.6-Luna', 'track': 'Vision',
            'expected_cases': expected_cases, 'counts': dict(statuses), 'valid_rgb': valid_rgb,
            'original_protocol_pass': valid_protocol, 'infrastructure_interruptions': infra,
            'pending': max(0, expected_cases-len(manifests)), 'usage_measured_cases': len(rows),
            'mean_input': mean([r['input'] for r in rows]), 'mean_output': mean([r['output'] for r in rows]),
            'mean_cached_input': mean([r['cached'] for r in rows]),
            'mean_reasoning_output': mean([r['reasoning'] for r in rows]),
            'mean_seconds': mean([r['duration_seconds'] for r in rows]),
            'mean_cost_measured_cache': mean([r['cost_measured_cache'] for r in rows]),
            'mean_cost_normalized70': mean([r['cost_normalized70'] for r in rows]),
            'cost_usage_complete_for_closed_attempts': cost_complete,
            'measured_protocol_deliveries': measured_deliveries,
            'cost_per_protocol_delivery_normalized70': (sum(r['cost_normalized70'] for r in rows)/measured_deliveries
                if measured_deliveries and cost_complete else None),
            'cost_per_protocol_delivery_measured_cache': (sum(r['cost_measured_cache'] for r in rows)/measured_deliveries
                if measured_deliveries and cost_complete else None),
            'scored_cases': len(current), 'mean_v1': mean([v['v1'] for v in current.values()]),
            'mean_v2': mean([v['v2'] for v in current.values()]),
            'paired': paired_quality(scored.get(reference_key, {}), current, scenario) if key != reference_key else None,
            'trace': {'cases': len(trace_rows),
                'mean_model_requests': mean([r.get('model_requests') for r in trace_rows]),
                'mean_exec_calls': mean([r.get('calls', {}).get('exec_command', 0) for r in trace_rows]),
                'mean_patch_calls': mean([r.get('calls',{}).get('apply_patch',0) for r in trace_rows]),
                'mean_image_calls': mean([sum(r.get('image_calls', {}).values()) for r in trace_rows]),
                'mean_returned_image_messages': mean([r.get('image_results', {}).get('returned_image_messages', 0) for r in trace_rows]),
                'mean_image_helper_failures': mean([r.get('image_results', {}).get('helper_digest_failures', 0) for r in trace_rows]),
                'mean_initial_image_reviews': mean([r.get('image_calls', {}).get('input', 0) for r in trace_rows]),
                'mean_tool_text_bytes': mean([r.get('tool_text_bytes') for r in trace_rows]),
                'mean_history_amplification_proxy': mean([r.get('history_amplification_proxy') for r in trace_rows]),
                'source_reads': {name: sum(r.get('source_reads', {}).get(name, 0) for r in trace_rows)
                    for name in ('artifact_io.py', 'player_template.html', 'INPUT_SUMMARY.json', 'pixelworld_run.py', 'pixelworld_check.py')}}}
        arms.append(entry)
    from datetime import datetime, timezone
    result = {'schema': 'pixelworld-flash-development-v2', 'updated_utc': datetime.now(timezone.utc).isoformat(),
        'scope': ('Independent Physics-IQ subset with verified working image tools' if experiment else 'Physics-IQ 20-case development subset with failed output image tools; not the 198-case Verified score'),
        'cohort': 'confirmation' if experiment else 'legacy-development',
        'mask_assignment': quality.get('mask_assignment'), 'canvas': '1280x720,24fps,120frames',
        'quality_scoring_fps':quality.get('scoring_fps',24 if quality_checkpoint else 4),
        'quality_scoring_frames':quality.get('scoring_frames',120 if quality_checkpoint else 20),
        'quality_temporal_sampling':quality.get('temporal_sampling','native submission frames' if quality_checkpoint else 'integer decimation to4FPS'),
        'reference_tariff_usd_per_m': {'uncached_input': 10, 'cached_input': 1, 'output': 50},
        'normalized_cache_rate': 0.7, 'quality_target': 'Confirm with independent held-out cases; proposed relative loss tolerance 5%',
        'accepted_flash': None, 'arms': arms}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary.replace(output)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--descriptions', type=Path)
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    report = build(args.root, args.output, args.descriptions, args.cache)
    print(json.dumps({'arms': len(report['arms']), 'mask_assignment': report['mask_assignment']}))
