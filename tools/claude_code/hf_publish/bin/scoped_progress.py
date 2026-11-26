"""Scoped replacement for tools.watch_hf_dataset_progress (step 3) for a contributor bundle.

Why: tools.watch_dashboard_publication.hf_progress rewrites GLOBAL dataset files
(live/catalogue.json, metadata/catalogue.json, live/experiment_plan.json,
live/evaluation.json, live/publication_coverage.json,
metadata/publication_coverage.json) from the local bundle only.  Run from our
bundle it would replace the owner's catalogue of ~150 groups with our 4-8 and
it also hard-requires publication/pixelworld/experiment_plan.json, which the
exporter never writes.

This worker reuses the same payload logic (sanitize + hf_* readiness flags +
tools.publication_coverage.coverage) but uploads ONLY files we own:
  live/<suite>/<model>/vision-agent/index.json   (one per Claude group)
and keeps the catalogue fragment and coverage locally:
  state/catalogue_fragment.json  state/publication_coverage.json
for the owner to merge into the global catalogue / main-suite snapshot.
Each commit is read back byte-for-byte at the returned revision.
"""
import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

DATASET = 'HuggingFriends/PixelWorld'


def build(source, bundle):
    from tools.prepare_hf_delivery import sanitize, TRACE_FORMAT_VERSION
    from tools.publication_coverage import coverage
    catalogue = sanitize(json.loads((source / 'catalogue.json').read_text()))
    catalogue['detail_base'] = f'https://huggingface.co/datasets/{DATASET}/resolve/main/live/'
    ledger_path = bundle / 'state/hf_incremental.json'
    ledger = json.loads(ledger_path.read_text()).get('ready', {}) if ledger_path.is_file() else {}
    items, indexes = [], {}
    for group in catalogue['groups']:
        payload = sanitize(json.loads((source / group['path']).read_text()))
        synced = current_traces = 0
        for history in payload.get('cases', {}).values():
            for attempt in history:
                ready = ledger.get(attempt['detail'])
                current = attempt.get('detail_sha256')
                attempt['hf_detail_ready'] = bool(ready and (not current or current == ready['source_sha256']))
                attempt['hf_trace_ready'] = bool(attempt['hf_detail_ready'] and ready.get('trace_ready'))
                attempt['hf_trace_format_version'] = ready.get('trace_format_version') if ready else None
                attempt['hf_trace_current_format'] = bool(attempt['hf_trace_ready'] and
                                                          ready.get('trace_format_version', 0) >= TRACE_FORMAT_VERSION)
                current_traces += attempt['hf_trace_current_format']
                synced += attempt['hf_detail_ready']
        payload['publication'] = {'hf_synced_details': synced, 'hf_current_format_traces': current_traces,
                                  'required_trace_format': TRACE_FORMAT_VERSION}
        group['publication'] = payload['publication']
        indexes[group['path']] = payload
        items.append(('live/' + group['path'], payload))
    cov = coverage(catalogue, indexes, {'ready': ledger})
    return items, catalogue, cov


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in ('updated_utc', 'generation_updated_utc')}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1)); tmp.replace(path)


def cycle(source, bundle, state_path, api, dry_dir=None):
    items, catalogue, cov = build(source, bundle)
    for path, _ in items:  # hard guard: only our own group index files
        parts = path.split('/')
        if len(parts) != 5 or parts[0] != 'live' or parts[-1] != 'index.json' or 'claude' not in parts[1]:
            raise ValueError('Refusing to publish outside our group namespace: ' + path)
    atomic(bundle / 'state/catalogue_fragment.json', catalogue)
    atomic(bundle / 'state/publication_coverage.json', cov)
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    acked = state.get('digests', {})
    changed, digests = [], {}
    for name, data in items:
        digests[name] = hashlib.sha256(json.dumps(stable(data), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if acked.get(name) != digests[name]:
            changed.append((name, json.dumps(data, ensure_ascii=False).encode()))
    if dry_dir is not None:
        for name, body in changed:
            target = dry_dir / 'staged' / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
        return {'dry_run': True, 'would_upload': [n for n, _ in changed], 'totals': cov['totals']}
    if changed:
        from huggingface_hub import CommitOperationAdd, hf_hub_download
        from tools.hf_commit_budget import commit
        result = commit(api, repo_id=DATASET, repo_type='dataset',
                        operations=[CommitOperationAdd(path_in_repo=n, path_or_fileobj=b) for n, b in changed],
                        commit_message='Refresh Claude main-suite live progress indexes')
        for name, body in changed:
            local = hf_hub_download(DATASET, name, repo_type='dataset', revision=result.oid, token=api.token)
            if Path(local).read_bytes() != body:
                raise ValueError('Readback mismatch for ' + name)
        state = {'digests': {**acked, **{n: digests[n] for n, _ in changed}}, 'commit': result.oid,
                 'verified_unix': time.time()}
        atomic(state_path, state)
    return {'changed': len(changed), 'commit': state.get('commit'), 'totals': cov['totals']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--token-file', type=Path)
    p.add_argument('--interval', type=int, default=180)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    if a.dry_run:
        dry = a.state.parent  # -> <dir>/staged/live/...
        print(json.dumps(cycle(a.source, a.bundle, a.state, None, dry)), flush=True)
        return
    from huggingface_hub import HfApi
    api = HfApi(token=a.token_file.read_text().strip())
    with a.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                out = {'ok': True, **cycle(a.source, a.bundle, a.state, api)}
            except Exception as error:
                out = {'ok': False, 'error_type': type(error).__name__, 'error': str(error)[:300]}
            print(json.dumps({'checked_unix': time.time(), **out}), flush=True)
            time.sleep(max(60, a.interval))


if __name__ == '__main__':
    main()
