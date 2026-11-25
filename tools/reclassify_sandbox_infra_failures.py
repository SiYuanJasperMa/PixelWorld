"""Reclassify positively recorded sandbox executable failures; preserve originals.

Zero frames and a missing named runtime root are insufficient. Require a failed
native command result naming an existing Codex executable hidden by the frozen
profile, and no successful native command. Discovery never enters case contents.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from pixelworld.util import atomic_json, sha256, utc_now

REASON = 'sandbox_profile_missing_runtime_roots'
STOP_REASON = 'local_cli_configuration_failure'
SINCE = '2026-11-24T09:20:00'
BACKUP = 'logs/run_manifest_before_sandbox_infra_reclassification.json'
EXEC_ERROR = re.compile(r'^bwrap: execvp (?P<binary>/[^\r\n]+/codex): No such file or directory\s*$')
INFRA_REASONS = ('provider_queue_exceeded', 'provider_connection_failed', 'provider_first_token_stall',
                 'storage_infrastructure_failure', 'local_proxy_request_limit', 'local_proxy_auth_failure',
                 STOP_REASON)
TRACKS = ('text', 'vision-input', 'vision-agent')


def discover_manifests(root: Path):
    """Read only root / optional benchmark-condition / model / track / case leaves."""
    paths = set()
    if (root / 'run_manifest.json').is_file():
        return [root / 'run_manifest.json']
    paths.update(root.glob('case_*/run_manifest.json'))
    if root.name in TRACKS:
        return sorted(path for path in paths if not path.is_symlink())
    # At most two hierarchy prefixes before model/track, never recursive glob.
    for prefix in ('', '*/', '*/*/'):
        for track in TRACKS:
            paths.update(root.glob(f'{prefix}*/{track}/case_*/run_manifest.json'))
    return sorted(path for path in paths if not path.is_symlink())


def _visible(path: Path, profile: dict) -> bool:
    entries = profile.get('filesystem', {})
    if not isinstance(entries, dict):
        return True  # Unknown profiles cannot prove a hidden executable.
    matches = []
    for key, mode in entries.items():
        if not isinstance(key, str) or not key.startswith('/'):
            continue
        root = Path(key)
        if path == root or root in path.parents:
            matches.append((len(root.parts), mode))
    if matches:
        return max(matches, key=lambda x: x[0])[1] in ('read', 'write')
    # :minimal supplies ordinary system mounts, not arbitrary home/cloud roots.
    return entries.get(':minimal') in ('read', 'write') and path.parts[1:2] in (
        ('usr',), ('bin',), ('sbin',), ('lib',), ('lib64',))


def _command_result(event: dict) -> dict | None:
    if event.get('type') == 'item.completed':
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') == 'command_execution':
            return item
    if event.get('type') == 'event_msg':
        payload = event.get('payload') or {}
        item = payload.get('item')
        if payload.get('type') == 'item_completed' and isinstance(item, dict) and item.get('type') == 'CommandExecution':
            return item
    return None


def sandbox_evidence(run: Path, manifest: dict) -> tuple[dict | None, str]:
    """Audit raw native results, never model prose, tool inputs, or derived labels."""
    profile_path = run / 'permissions_profile.json'
    try:
        profile = json.loads(profile_path.read_text())
    except (OSError, ValueError):
        return None, 'no-profile'
    digest = sha256(profile_path)
    if (manifest.get('permissions_profile') or {}).get('sha256') != digest:
        return None, 'unverified-profile'
    if not isinstance(profile.get('filesystem'), dict):
        return None, 'unknown-profile'
    source = run / 'logs/events.raw.jsonl'
    if not source.is_file():
        source = run / 'logs/full_rollout.jsonl'
    if not source.is_file() or source.is_symlink():
        return None, 'no-native-command-results'
    errors, commands, successes, malformed = [], 0, 0, 0
    # Closed raw stdout is small; full native rollouts are streamed, not loaded.
    try:
        with source.open() as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(event, dict):
                    malformed += 1
                    continue
                item = _command_result(event)
                if item is None:
                    continue
                commands += 1
                if item.get('exit_code') == 0 or item.get('status') in ('completed', 'succeeded'):
                    successes += 1
                match = EXEC_ERROR.fullmatch(item.get('aggregated_output') or '')
                if match and item.get('status') == 'failed' and type(item.get('exit_code')) is int and item['exit_code'] != 0:
                    errors.append({'line': line_number, 'binary': match['binary'],
                                   'exit_code': item['exit_code'], 'item_id': item.get('id')})
    except OSError:
        return None, 'unreadable-native-command-results'
    if malformed:
        return None, 'malformed-native-command-results'
    if successes:
        return None, 'successful-native-command-present'
    if not errors:
        return None, 'no-recorded-bwrap-execvp-error'
    targets = []
    for name in sorted({x['binary'] for x in errors}):
        binary = Path(name)
        try:
            resolved = binary.resolve(strict=True)
        except OSError:
            return None, 'runtime-binary-unavailable'
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None, 'runtime-binary-not-executable'
        if _visible(binary, profile) and _visible(resolved, profile):
            return None, 'runtime-binary-visible'
        targets.append({'binary': name, 'resolved_binary': str(resolved),
                        'profile_visible': _visible(binary, profile),
                        'resolved_profile_visible': _visible(resolved, profile)})
    return {'native_results': str(source.relative_to(run)), 'native_results_sha256': sha256(source),
            'profile': 'permissions_profile.json', 'profile_sha256': digest,
            'native_command_count': commands, 'successful_native_commands': successes,
            'execvp_errors': errors, 'runtime_targets': targets}, 'recorded-hidden-runtime-execvp-error'


def eligible(run: Path, manifest: dict, since: str) -> tuple[bool, str]:
    if manifest.get('status') != 'failed':
        return False, 'not-failed'
    if (manifest.get('started_utc') or '') < since:
        return False, 'before-window'
    if manifest.get('stop_reason') in INFRA_REASONS:
        return False, 'already-infra'
    validation = manifest.get('validation') or {}
    if type(validation.get('frames')) is not int or validation['frames'] != 0:
        return False, 'frames-present-or-unknown'
    evidence, reason = sandbox_evidence(run, manifest)
    return evidence is not None, reason


def _preserve_original(path: Path, original: bytes) -> Path:
    backup = path.parent / BACKUP
    backup.parent.mkdir(parents=True, exist_ok=True)
    try:
        with backup.open('xb') as handle:
            os.chmod(backup, 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if backup.is_symlink() or backup.read_bytes() != original:
            raise ValueError('Existing original manifest backup differs; source was not changed')
    if backup.read_bytes() != original:
        raise ValueError('Original manifest backup readback failed')
    return backup


def reclassify(root: Path, since: str, apply: bool) -> dict:
    items, reasons = [], Counter()
    for path in discover_manifests(root):
        try:
            original = path.read_bytes()
            manifest = json.loads(original)
        except (OSError, ValueError):
            reasons['unreadable-manifest'] += 1
            continue
        ok, reason = eligible(path.parent, manifest, since)
        reasons[reason] += 1
        if not ok:
            continue
        evidence, _ = sandbox_evidence(path.parent, manifest)
        before = hashlib.sha256(original).hexdigest()
        record = {'schema': 'pixelworld-positive-sandbox-infra-v2', 'reason': REASON,
                  'detail': 'Native shell results recorded hidden Codex executable startup failure with zero frames',
                  'evidence': evidence, 'original_stop_reason': manifest.get('stop_reason'),
                  'original_manifest': BACKUP, 'original_manifest_sha256': before,
                  'reclassified_utc': utc_now()}
        manifest['stop_reason'] = STOP_REASON
        manifest.setdefault('validation', {})['infra_error'] = REASON
        manifest['infra_reclassification'] = record
        item = {'manifest': str(path), 'model': manifest.get('model_key'), 'case': manifest.get('case_id'),
                'attempt': manifest.get('attempt'), 'sha256_before': before, 'evidence': evidence}
        if apply:
            backup = _preserve_original(path, original)
            if path.read_bytes() != original:
                raise ValueError('Manifest changed during inspection; source was not changed')
            atomic_json(path, manifest)
            item.update(sha256_after=sha256(path), original_manifest=str(backup))
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
    atomic_json(args.report, combined)
    print(json.dumps({k: combined[k] for k in ('apply', 'eligible_total')}, indent=1))


if __name__ == '__main__':
    main()
