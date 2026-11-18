"""Publish factual single-case submission-tool progress without model requests."""
import argparse
import json
from pathlib import Path
import time

from pixelworld.util import atomic_json, utc_now
from tools.flash_attempts import histories, selected, accepted
from tools.watch_benchmark_services import alive_service


def snapshot(study):
    config = json.loads((study / 'configs/submit_smoke.json').read_text())
    model = config['models'][0]
    folder = Path(config['runs_root']) / model['key'] / 'vision-agent'
    cases = histories(folder)
    case = config['case_ids'][0]
    rows = cases.get(case, [])
    if not rows:
        return {'updated_utc': utc_now(), 'case': case, 'status': 'pending', 'active': False}
    path, record = selected(rows)
    run = path.parent
    active = record.get('status') == 'running' and alive_service({
        'pid_file': str(run/'logs/supervisor.pid'),
        'identity': ['pixelworld.cli', '--case', case, '--attempt', str(record['attempt'])]})
    calls = set()
    events = run/'logs/events.jsonl'
    if events.exists():
        for line in events.open():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get('item', {})
            if item.get('type') == 'mcp_tool_call' and item.get('tool') == 'submit_video':
                calls.add(item.get('id'))
    receipt_path = run/'logs/submission_receipt.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    score_path = study/'quality/smoke_native24.json'
    score = json.loads(score_path.read_text()) if score_path.exists() else {}
    scored = bool(accepted(record) and score.get('pixels_sha256') == record.get('pixels_sha256'))
    usage = record.get('token_usage') or {}
    return {'updated_utc': utc_now(), 'case': case, 'model': model['model'],
            'status': record['status'], 'active': bool(active), 'attempt': record['attempt'],
            'attempt_records': len(rows), 'valid_delivery': bool(accepted(record)),
            'submission_tool_calls': len(calls), 'submission_accepted': receipt.get('accepted', False),
            'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
            'usage_status': usage.get('status'), 'score': {k: score[k] for k in ('v1','v2')} if scored else None,
            'scored': scored, 'scope': 'One-case mechanism smoke; no accepted Flash version'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--web-root', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=15)
    args=parser.parse_args()
    while True:
        row=snapshot(args.study)
        atomic_json(args.study/'analysis/submission_probe.json', row)
        atomic_json(args.web_root/'flash_submission_probe.json', row)
        print(json.dumps(row), flush=True)
        if row.get('scored'):
            break
        time.sleep(args.interval)
