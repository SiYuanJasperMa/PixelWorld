"""Read accepted runs from an operator map and apply explicitly supplied tariffs.

This tool never launches generation or changes run records. Unknown usage and
missing tariffs remain unknown. Failed-attempt and evaluator costs are separate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import time

from pixelworld.config import load_config
from pixelworld.supervisor import inspect
from pixelworld.util import atomic_json, sha256, utc_now


def number(value):
    return type(value) is int and value >= 0


def recorded_usage(run):
    manifest = json.loads((run / 'run_manifest.json').read_text())
    usage = manifest.get('token_usage') or {}
    if usage.get('status') in ('partial', 'missing', 'unknown') or not all(
            number(usage.get(k)) for k in ('input_tokens', 'output_tokens')):
        return None, []
    requests, seen = [], {}
    ledger = run / 'recording/api_usage.jsonl'
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                return usage, []
            if row.get('record_kind') != 'api_request_usage':
                continue
            rid, value = row.get('response_id'), row.get('usage') or {}
            if not isinstance(rid, str) or not rid:
                return usage, []
            if rid in seen:
                if seen[rid] != value:
                    return usage, []
                continue
            if not all(number(value.get(k)) for k in ('input_tokens', 'output_tokens')):
                return usage, []
            seen[rid] = value
            requests.append(value)
    if not requests or any(sum(v[k] for v in requests) != usage[k]
                           for k in ('input_tokens', 'output_tokens')):
        requests = []
    return usage, requests


def estimate(usage, requests, tariff, hit_rate):
    if not tariff:
        return None, 'Documented model tariff unavailable', 0
    for key in ('input', 'cached_input', 'output'):
        value = tariff.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Explicit finite nonnegative input/cache/output tariffs required')
    if not isinstance(tariff.get('source'), str) or not tariff['source'].startswith('https://'):
        raise ValueError('Tariff must identify its source URL')
    threshold = tariff.get('long_context_input_tokens')
    if threshold is not None and (not number(threshold) or threshold <= 0):
        raise ValueError('Positive long-context threshold required')
    long_input = tariff.get('long_context_input_multiplier', 1)
    long_output = tariff.get('long_context_output_multiplier', 1)
    for multiplier in (long_input, long_output):
        if type(multiplier) not in (int, float) or not math.isfinite(multiplier) or multiplier < 1:
            raise ValueError('Finite context multipliers of at least one required')
    values = requests or [usage]
    total, premium = 0.0, 0
    for value in values:
        long = bool(requests and threshold is not None and value['input_tokens'] > threshold)
        premium += long
        input_rate = ((1 - hit_rate) * tariff['input'] + hit_rate * tariff['cached_input'])
        total += (value['input_tokens'] * input_rate * (long_input if long else 1)
                  + value['output_tokens'] * tariff['output'] * (long_output if long else 1)) / 1e6
    audit = ('Per-request input/output sums equal accepted terminal counters' if requests else
             'Base-rate estimate; per-request totals unavailable or disagree with terminal counters')
    return total, audit, premium


def cohort(entry, tariffs, hit_rate):
    config = load_config(Path(entry['config']))
    model = config.model(entry['model_key'])
    expected = len(config.case_ids)
    if expected != entry['expected']:
        raise ValueError('Registered case scope differs from the config')
    counts, inputs, outputs, cached, costs, audits = Counter(), [], [], [], [], Counter()
    tariff = tariffs.get(model.key)
    premium = 0
    for case in config.case_ids:
        selected = inspect(config, model, entry['track'], case)
        counts[selected['state']] += 1
        if selected['state'] != 'complete':
            continue
        run = Path(selected['run'])
        before = sha256(run / 'run_manifest.json')
        usage, requests = recorded_usage(run)
        if before != sha256(run / 'run_manifest.json'):
            usage, requests = None, []
        if usage is None:
            audits['missing_usage'] += 1
            continue
        inputs.append(usage['input_tokens'])
        outputs.append(usage['output_tokens'])
        value = usage.get('cached_input_tokens')
        if number(value) and value <= usage['input_tokens']:
            cached.append((usage['input_tokens'], value))
        cost, audit, long = estimate(usage, requests, tariff, hit_rate)
        premium += long
        audits[audit] += 1
        if requests:
            audits['per_request_verified_cases'] += 1
        if cost is not None:
            costs.append(cost)
    complete, measured = counts['complete'], len(inputs)
    condition = entry['condition']
    if entry['kind'] == 'main':
        condition = {'pcve': 'standard720p24fps', 'phygenbench': 't2v720p24fps'}.get(
            entry['benchmark'], 'main720p24fps')
    row = {'benchmark': entry['benchmark'], 'condition': condition, 'model': model.key,
           'track': entry['track'], 'complete': complete, 'expected': expected,
           'generation_counts': dict(counts), 'usage_missing_cases': complete - measured,
           'mean_input_tokens': sum(inputs) / measured if measured else None,
           'mean_output_tokens': sum(outputs) / measured if measured else None,
           'cache_complete_cases': len(cached),
           'reported_cache_hit_rate': (sum(v[1] for v in cached) / sum(v[0] for v in cached)
                                       if cached and sum(v[0] for v in cached) else None),
           'per_call_records_cases': audits['per_request_verified_cases'],
           'premium_requests': premium, 'price_source': tariff.get('source') if tariff else None,
           'price_document_sha256': tariff.get('document_sha256') if tariff else None,
           'cost_audit': dict(audits), 'assumed_cache_hit_rate': hit_rate,
           'standardized_cache70_cost_usd': (sum(costs) / complete
                                           if complete and len(costs) == complete else None),
           'cost_scope': 'accepted delivery only; failed attempts and evaluator costs excluded'}
    return row


def build(map_path, tariffs, progress=None, hit_rate=.7):
    if hit_rate != .7:
        raise ValueError('The published standardized-cache70 field requires a 70% assumption')
    data = json.loads(map_path.read_text())
    rows, seen = [], set()
    for entry in data['cohorts']:
        if entry['kind'] not in ('main', 'analysis'):
            continue
        identity = entry['condition'], entry['model_key'], entry['track']
        if identity in seen:
            raise ValueError('Duplicate registered cohort')
        seen.add(identity)
        rows.append(cohort(entry, tariffs, hit_rate))
        if progress:
            atomic_json(progress, {'updated_utc': utc_now(), 'groups_done': len(rows),
                                   'latest': identity, 'source_records_modified': False})
    return {'updated_at': time.time(), 'updated_utc': utc_now(), 'rows': rows,
            'operator_map_sha256': sha256(map_path), 'assumed_cache_hit_rate': hit_rate,
            'cost_definition': 'Estimated accepted-delivery API cost with documented tariffs and 70% cache. '
                'Long-context premiums use matching per-request totals; base-rate fallback is labeled. '
                'Cache writes, failed attempts, tools/GPU and evaluator costs excluded.',
            'source_records_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map', type=Path, required=True)
    parser.add_argument('--prices', type=Path, required=True,
                        help='Explicit model tariff mapping with a source URL for every priced model')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--progress', type=Path)
    args = parser.parse_args()
    result = build(args.map.resolve(), json.loads(args.prices.read_text()), args.progress)
    atomic_json(args.output, result)
    print(json.dumps({'groups': len(result['rows']), 'updated_utc': result['updated_utc']}))


if __name__ == '__main__':
    main()
