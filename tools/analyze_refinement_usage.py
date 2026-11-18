"""Measure usage reported after the first saved deliverable, without generation."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from pixelworld.util import atomic_json


def timestamp(value):
    time = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if time.tzinfo is None:
        raise ValueError('Usage boundaries require timezone-aware timestamps')
    return time


def measured_usage(path, cutoff, expected):
    """Deduplicate cumulative events and require agreement with terminal usage."""
    cutoff = timestamp(cutoff)
    seen, events = set(), []
    for line in path.open():
        record = json.loads(line)
        payload = record.get('payload') or {}
        if record.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            continue
        info = payload.get('info') or {}
        total = info.get('total_token_usage') or {}
        key = tuple(total.get(k) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens'))
        usage = info.get('last_token_usage') or {}
        if not usage.get('input_tokens') or key in seen:
            continue
        seen.add(key)
        events.append((timestamp(record['timestamp']), usage))
    totals = {k: sum(u.get(k, 0) for _, u in events) for k in ('input_tokens', 'output_tokens')}
    if not events or any(totals[k] != expected[k] for k in totals):
        raise ValueError('Request usage does not match terminal input/output usage')
    after = [u for time, u in events if time > cutoff]
    return {**totals, 'requests': len(events), 'requests_reported_after': len(after),
            'input_reported_after': sum(u['input_tokens'] for u in after),
            'output_reported_after': sum(u.get('output_tokens', 0) for u in after)}


def build(cohort):
    refinement = json.loads((cohort / 'quality/refinement_native24.json').read_text())
    rows = []
    for row in refinement['per_case']:
        if row.get('status') != 'scored':
            continue
        run = cohort / ('generations/baseline/gpt56-luna/vision-agent/case_' + row['case'] + '_attempt_01')
        manifest = json.loads((run / 'run_manifest.json').read_text())
        snapshot = json.loads((run / f"trace/round_{row['first_snapshot_round']:03d}/snapshot.json").read_text())
        if (manifest['pixels_sha256'] != row['final_pixels_sha256']
                or snapshot['pixels_sha256'] != row['first_pixels_sha256']
                or manifest['token_usage'].get('status') != 'recorded'):
            raise ValueError('Usage analysis requires the scored first/final identities and complete usage')
        usage = measured_usage(run / 'logs/full_rollout.jsonl', snapshot['utc'], manifest['token_usage'])
        rows.append({'case': row['case'], 'first_pixels_sha256': row['first_pixels_sha256'],
                     'final_pixels_sha256': row['final_pixels_sha256'], 'first_snapshot_utc': snapshot['utc'],
                     'seconds_after': (timestamp(manifest['completed_utc']) - timestamp(snapshot['utc'])).total_seconds(),
                     **usage})
    output = {'scope': 'Usage reported strictly after the first saved deliverable snapshot',
              'limitation': 'Completion timestamps; includes checking, correction and validation. Not pure revision cost or proven avoidable spending.',
              'per_case': rows}
    atomic_json(cohort / 'analysis/post_first_delivery_usage.json', output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    result = build(parser.parse_args().cohort)
    print(json.dumps({'measured_cases': len(result['per_case'])}))
