"""Reclassify attempts invalidated by the missing-runtime bwrap profile.

The supervisor must run on the packaged runtime Python: `case_permissions`
derives its read roots from `sys.executable`. A supervisor launched with the
system Python produced a profile without the runtime `bin/lib` roots, so every
agent shell command failed with `bwrap: execvp .../codex: No such file or
directory` before the agent could read the prompt or write frames.

This tool only reclassifies an attempt when the deterministic evidence is
present: the attempt failed with zero frames, and its frozen
`permissions_profile.json` contains no runtime root. It never changes frames,
code, usage or the original validation fields; it adds an explicit
`infra_reclassification` record and the existing infra stop reason so retry
accounting exempts the attempt from the model-attempt cap.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from pixelworld.util import atomic_json, sha256, utc_now

REASON = 'sandbox_profile_missing_runtime_roots'
STOP_REASON = 'local_cli_configuration_failure'
SINCE = '2026-11-24T09:20:00'


def runtime_rooted(profile: dict) -> bool:
    return any('runtime_main_worker' in str(key) for key in profile.get('filesystem', {}))


def eligible(run: Path, manifest: dict, since: str) -> tuple[bool, str]:
    if manifest.get('status') != 'failed':
        return False, 'not-failed'
    if (manifest.get('started_utc') or '') < since:
        return False, 'before-window'
    if manifest.get('stop_reason') in (
            'provider_queue_exceeded', 'provider_connection_failed', 'provider_first_token_stall',
            'storage_infrastructure_failure', 'local_proxy_request_limit', 'local_proxy_auth_failure',
            'local_cli_configuration_failure'):
        return False, 'already-infra'
    validation = manifest.get('validation') or {}
    if validation.get('frames') != 0:
        return False, 'frames-present'
    profile_path = run / 'permissions_profile.json'
    try:
        profile = json.loads(profile_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False, 'no-profile'
    if runtime_rooted(profile):
        return False, 'runtime-rooted'
    return True, 'missing-runtime-roots'


def reclassify(root: Path, since: str, apply: bool) -> dict:
    items = []
    reasons = Counter()
    for path in sorted(root.rglob('run_manifest.json')):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        ok, reason = eligible(path.parent, manifest, since)
        reasons[reason] += 1
        if not ok:
            continue
        before = sha256(path)
        record = {
            'reason': REASON,
            'detail': 'bwrap read roots omitted the packaged runtime; every agent shell command failed before frames were written',
            'evidence': ['permissions_profile.json lacks runtime_main_worker roots', 'validation.frames == 0'],
            'original_stop_reason': manifest.get('stop_reason'),
            'reclassified_utc': utc_now(),
        }
        manifest['stop_reason'] = STOP_REASON
        validation = manifest.setdefault('validation', {})
        validation['infra_error'] = REASON
        manifest['infra_reclassification'] = record
        item = {'manifest': str(path), 'model': manifest.get('model_key'),
                'case': manifest.get('case_id'), 'attempt': manifest.get('attempt'),
                'sha256_before': before}
        if apply:
            atomic_json(path, manifest)
            item['sha256_after'] = sha256(path)
        items.append(item)
    return {'since': since, 'apply': apply, 'roots': [str(root)],
            'eligible': len(items), 'skipped': dict(reasons), 'items': items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--since', default=SINCE)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    combined = {'apply': args.apply, 'since': args.since, 'reports': []}
    for root in args.root:
        if not root.is_dir():
            raise SystemExit(f'Missing root: {root}')
        combined['reports'].append(reclassify(root, args.since, args.apply))
    combined['eligible_total'] = sum(r['eligible'] for r in combined['reports'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.report, combined)
    print(json.dumps({k: combined[k] for k in ('apply', 'eligible_total')}, indent=1))


if __name__ == '__main__':
    main()
