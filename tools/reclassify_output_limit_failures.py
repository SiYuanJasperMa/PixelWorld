"""Recover only raw-API-verified failures caused by a proxy's smaller fallback.

The operator supplies an audited run list and exact proxy roots. Every changed
manifest keeps its byte-identical predecessor and SHA-bound native evidence.
Nothing is moved, renumbered, or counted as a successful generation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from pixelworld.retries import infrastructure_failure
from pixelworld.util import atomic_json, sha256, utc_now

BACKUP = 'logs/run_manifest_before_output_limit_reclassification.json'


def evidence(run: Path, proxy: Path, faulty_limit: int = 32000) -> dict | None:
    manifest = run/'run_manifest.json'
    m = json.loads(manifest.read_text())
    if (m.get('status') != 'failed' or infrastructure_failure(m)
            or m.get('stop_reason') == 'output_token_budget_exhausted'
            or type(m.get('output_token_budget')) is not int
            or m['output_token_budget'] <= faulty_limit):
        return None
    output = m.get('token_usage', {}).get('output_tokens')
    if type(output) is not int or output >= m['output_token_budget']:
        return None
    ledger = run/'recording/api_usage.jsonl'
    if not ledger.exists():
        return None
    calls = [json.loads(line) for line in ledger.read_text().splitlines()]
    if not calls:
        return None
    rid = calls[-1].get('response_id')
    if not isinstance(rid, str) or not re.fullmatch(r'resp_[A-Za-z0-9]+', rid):
        return None
    files = {role: proxy/f'{rid}.{role}.json' for role in ('input', 'native', 'events', 'response')}
    if any(not p.is_file() or p.is_symlink() for p in files.values()):
        return None
    raw = {role: json.loads(p.read_text()) for role, p in files.items()}
    if raw['response'].get('status')!='completed':
        return None  # A correctly reported provider limit is not this adapter bug.
    if (raw['input'].get('max_output_tokens') is not None
            or raw['native'].get('max_tokens') != faulty_limit):
        return None  # An intentional client cap is not this proxy bug.
    done = [e.get('data') for e in raw['events'] if e.get('event') == 'done']
    usage = [e.get('data') for e in raw['events'] if e.get('event') == 'token_usage']
    if (not done or not isinstance(done[-1], dict)
            or done[-1].get('finish_reason') != 'length'
            or not usage or usage[-1].get('completion_tokens') != faulty_limit
            or calls[-1].get('usage', {}).get('output_tokens') != faulty_limit):
        return None
    return {'response_id': rid, 'faulty_default_max_output_tokens': faulty_limit,
            'case_output_token_budget': m['output_token_budget'],
            'adapter_reported_status':'completed', 'expected_status':'incomplete',
            'native_finish_reason': 'length', 'api_usage_ledger_sha256': sha256(ledger),
            'raw_files': {role: {'path': str(p), 'sha256': sha256(p)} for role, p in files.items()}}


def reclassify(run: Path, proxy: Path, *, apply=False, faulty_limit=32000) -> dict:
    manifest = run/'run_manifest.json'
    proof = evidence(run, proxy, faulty_limit)
    row = {'source_run': str(run), 'eligible': proof is not None, 'applied': False}
    if proof is None:
        return row
    row['evidence'] = proof
    before = manifest.read_bytes()
    row['manifest_sha256_before'] = sha256(manifest)
    if apply:
        backup = run/BACKUP
        with backup.open('xb') as f:
            f.write(before)
        backup.chmod(0o600)
        if backup.read_bytes() != before:
            raise ValueError('Original manifest readback mismatch')
        m = json.loads(before)
        m['validation']['infra_error'] = 'proxy_marked_truncated_response_completed'
        m.update(stop_reason='proxy_marked_truncated_response_completed', retryable=True)
        m['output_limit_reclassification'] = {
            'schema': 'pixelworld-positive-output-limit-infra-v1', 'updated_utc': utc_now(),
            'original_manifest': BACKUP, 'original_manifest_sha256': row['manifest_sha256_before'],
            'evidence': proof, 'code_or_output_changed': False,
        }
        atomic_json(manifest, m)
        row['applied'] = True
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--audit', type=Path, required=True)
    p.add_argument('--proxy-root', type=Path, action='append', required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    rows = []
    for record in json.loads(args.audit.read_text())['records']:
        if record.get('category') != 'truncated_response':
            continue
        run = Path(record['source_run'])
        row = {'source_run': str(run), 'eligible': False, 'applied': False}
        for proxy in args.proxy_root:
            row = reclassify(run, proxy, apply=args.apply)
            if row['eligible']:
                break
        rows.append(row)
    report = {'updated_utc': utc_now(), 'apply': args.apply,
              'eligible': sum(r['eligible'] for r in rows),
              'applied': sum(r['applied'] for r in rows), 'records': rows}
    atomic_json(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}))


if __name__ == '__main__':
    main()
