"""Run the repo's tools.publish_hf_incremental cycle unchanged, but record per-file receipts.

The upstream uploader's ledger (state/hf_incremental.json) stores only the commit
oid per case.  To allow a later byte-level readback (bin/verify_published.py),
this wrapper intercepts tools.hf_commit_budget.commit and, for every
CommitOperationAdd, appends {path_in_repo, size, sha256, git_sha1} to
state/upload_receipts.jsonl - once as 'pending' before the commit and once as
'committed' with the returned oid.

--dry-run: no HfApi is created and nothing is sent.  The would-be commit's
operations are materialised under <dry-run-dir>/staged/<path_in_repo> and the
upstream ledger is written to <dry-run-dir>/hf_incremental.json (never to the
real state ledger).
"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path


def blob_bytes(op):
    src = op.path_or_fileobj
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    return Path(src).read_bytes()


def describe(op):
    data = blob_bytes(op)
    return {'path_in_repo': op.path_in_repo, 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'git_sha1': hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()}


def append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush(); os.fsync(stream.fileno())


# Fail-closed gate: block a commit whose text content (JSON, or any member of a
# trace tarball) matches a credential / private-path pattern.
import re, tarfile, io
LEAK = re.compile(rb'sk-ant-[A-Za-z0-9_-]{8,}|\bhf_[A-Za-z0-9]{20,}|/home/[A-Za-z0-9_.-]+/|/mnt/|'
                  rb'Bearer\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{12,}|\bghp_[A-Za-z0-9]{20,}|pixelworld_opus')


def leak_check(op):
    data = blob_bytes(op)
    name = op.path_in_repo
    if name.endswith('.tar.gz'):
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive.getmembers():
                if member.isfile() and not member.name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    hit = LEAK.search(archive.extractfile(member).read())
                    if hit:
                        raise ValueError(f'Leak pattern in {name}:{member.name}')
    elif name.endswith(('.json', '.txt', '.html', '.py', '.md')):
        if LEAK.search(data):
            raise ValueError(f'Leak pattern in {name}')


class FakeCommit:
    def __init__(self, oid):
        self.oid = oid


def install(receipts, dry_dir=None):
    import tools.hf_commit_budget as budget
    real_commit = budget.commit

    def wrapped(api, *args, **kwargs):
        ops = kwargs.get('operations') or []
        for op in ops:
            leak_check(op)
        files = [describe(op) for op in ops]
        batch = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
        base = {'batch': batch, 'repo_id': kwargs.get('repo_id'), 'repo_type': kwargs.get('repo_type'),
                'commit_message': kwargs.get('commit_message'), 'files': files}
        if dry_dir is not None:
            staged = dry_dir / 'staged'
            for op in ops:
                target = staged / op.path_in_repo
                target.parent.mkdir(parents=True, exist_ok=True)
                src = op.path_or_fileobj
                if isinstance(src, (bytes, bytearray)):
                    target.write_bytes(src)
                else:
                    shutil.copyfile(src, target)
            append(receipts, {**base, 'status': 'dry-run', 'utc': time.time(), 'commit': None})
            return FakeCommit('DRY-RUN-' + batch)
        append(receipts, {**base, 'status': 'pending', 'utc': time.time()})
        result = real_commit(api, *args, **kwargs)
        append(receipts, {**base, 'status': 'committed', 'utc': time.time(), 'commit': result.oid})
        return result

    budget.commit = wrapped


VOLATILE = ('media_stamp',)  # local mtimes of pixels.npz etc.; no public meaning


def equivalent_to_published(relative, acknowledged, package, trace_required):
    """True if the new public detail equals the one already committed except VOLATILE keys.

    Compared against the copy of the committed detail that verify_published.py already
    downloaded into the local HF cache (exact revision); if it is not cached, or the
    upload is not yet verified, we never skip.  Media are content-addressed
    (assets/<digest>/...), so equal detail JSON implies equal preview/poster files.
    """
    commit = acknowledged.get('commit')
    if not commit or not acknowledged.get('source_sha256'):
        return False
    if trace_required and not (acknowledged.get('trace_ready') and
                               acknowledged.get('trace_format_version', 0) >= (package.get('trace_format_version') or 0)):
        return False
    from huggingface_hub import try_to_load_from_cache
    cached = try_to_load_from_cache('HuggingFriends/PixelWorld', 'live/' + relative,
                                    revision=commit, repo_type='dataset')
    if not isinstance(cached, str):
        return False
    try:
        old = json.loads(Path(cached).read_text())
    except (OSError, ValueError):
        return False
    new = json.loads(package['detail'])
    strip = lambda d: {k: v for k, v in d.items() if k not in VOLATILE}
    return strip(old) == strip(new)


def multi_cycle(source, state_path, api, batch_size, max_bytes):
    """tools.publish_hf_incremental.publish_cycle, minus its one-case-per-group cap.

    Upstream breaks out of a group as soon as it selected ONE case, so a cycle commits
    at most len(groups) cases and in practice 1 (only Physics-IQ has a backlog): with
    --interval 900 that is ~4 cases/hour no matter what --batch-size says.  Here
    groups are visited round-robin (fair), each still in upstream order (latest
    completion first, then newest case first, same attempt priority), until
    batch_size cases or max_bytes of payload are selected; all of them go into ONE
    atomic commit with the same paths/ledger format, so the verifier, scoped_progress
    and offload are unchanged.
    """
    import tools.publish_hf_incremental as up
    from huggingface_hub import CommitOperationAdd
    state = up.read(state_path)
    if batch_size < 1:
        raise ValueError('Positive publication batch size required')
    ready = state.setdefault('ready', {})
    groups = up.read(source / 'catalogue.json').get('groups', [])
    if not groups:
        return state
    cursor = state.get('cursor', 0) % len(groups)
    delivery_priority = state.get('publication_round', 0) % 2 == 0
    trace_required = (source.parent.parent / 'live_sources.json').is_file()
    cache_root = (source.parent.parent / 'hf_incremental_traces').resolve()
    skipped = []

    def drop_trace(package):
        # Reproducible trace packages; remove after commit (upstream) or when not needed.
        for remote, path in package['media'].items():
            path = Path(path)
            if remote.endswith('.trace.tar.gz') and path.resolve().is_relative_to(cache_root):
                path.unlink(missing_ok=True)

    def candidates(group):
        histories = up.read(source / group['path']).get('cases') or {}
        latest = (group.get('latest_completion') or ['', ''])[1]
        ids = ([latest] if latest in histories else []) + [k for k in sorted(histories, reverse=True) if k != latest]
        for case in ids:
            if delivery_priority:
                ordered = sorted(histories[case], key=lambda a: (a.get('status') != 'complete', not a.get('reused', False),
                                 a['attempt'] if a.get('status') == 'complete' else -a['attempt']))
            else:
                ordered = sorted(histories[case], key=lambda a: -a['attempt'])
            for attempt in ordered:
                relative = attempt['detail']
                path = source / relative
                if not path.is_file() or attempt.get('status') == 'running':
                    continue
                raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                acknowledged = ready.get(relative, {})
                if acknowledged.get('source_sha256') == raw_hash and (not trace_required or
                        acknowledged.get('trace_ready') and acknowledged.get('trace_format_version', 0) >= up.TRACE_FORMAT_VERSION):
                    continue
                package = up.package_case(source, relative)
                if package is None:
                    continue
                if equivalent_to_published(relative, acknowledged, package, trace_required):
                    # Only exporter bookkeeping changed (offload.py's copy to exFAT resets the
                    # mtimes recorded in media_stamp): acknowledge locally, no commit.
                    acknowledged.update(source_sha256=raw_hash, equivalent_refresh_unix=time.time())
                    skipped.append(relative)
                    drop_trace(package)
                    break
                yield relative, package
                break  # one attempt per case, as upstream

    order = groups[cursor:] + groups[:cursor]
    iters = [candidates(g) for g in order]
    selected, assets, total = [], {}, 0
    while iters and len(selected) < batch_size and total < max_bytes:
        for it in list(iters):
            item = next(it, None)
            if item is None:
                iters.remove(it)
                continue
            relative, package = item
            selected.append(item)
            for name, path in package['media'].items():
                if name not in assets:
                    assets[name] = path
                    total += Path(path).stat().st_size
            total += len(package['detail'])
            if len(selected) >= batch_size or total >= max_bytes:
                break
    state['cursor'] = (cursor + 1) % len(groups)
    state['publication_round'] = state.get('publication_round', 0) + 1
    if selected:
        operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=path) for name, path in assets.items()]
        operations += [CommitOperationAdd(path_in_repo='live/' + relative, path_or_fileobj=package['detail'])
                       for relative, package in selected]
        from tools.hf_commit_budget import commit as dataset_commit
        commit = dataset_commit(api, repo_id='HuggingFriends/PixelWorld', repo_type='dataset',
                                operations=operations,
                                commit_message='Publish ready case videos and detailed traces together')
        now = time.time()
        for relative, package in selected:
            ready[relative] = {'source_sha256': package['source_sha256'],
                               'uploaded_unix': now, 'commit': commit.oid,
                               'media_files': len(package['media']), 'trace_ready': package['trace_ready'],
                               'trace_format_version': package.get('trace_format_version')}
        state['last_commit'] = commit.oid
        state['last_uploaded_unix'] = now
    state['checked_unix'] = time.time()
    state['last_cycle'] = {'selected': len(selected), 'bytes': total, 'equivalent_skipped': len(skipped)}
    temporary = state_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(state_path)
    for _, package in selected:
        drop_trace(package)
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--receipts', type=Path, required=True)
    p.add_argument('--token-file', type=Path)
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--batch-size', type=int, default=8)
    p.add_argument('--once', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--dry-run-dir', type=Path)
    p.add_argument('--max-bytes', type=int, default=600 * 1024 ** 2,
                   help='stop adding cases to a commit once its payload reaches this size')
    p.add_argument('--upstream-cycle', action='store_true',
                   help='use tools.publish_hf_incremental.publish_cycle (max 1 case per group per commit)')
    a = p.parse_args()
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    if a.upstream_cycle:
        from tools.publish_hf_incremental import publish_cycle
    else:
        def publish_cycle(source, state, api, batch_size):
            return multi_cycle(source, state, api, batch_size, a.max_bytes)
    if a.dry_run:
        dry = (a.dry_run_dir or a.state.parent / 'dryrun').resolve()
        dry.mkdir(parents=True, exist_ok=True)
        install(dry / 'upload_receipts.dryrun.jsonl', dry)
        state, api = dry / 'hf_incremental.json', None
        # Loop until no further ready record is packaged (each cycle takes <= batch-size).
        groups = max(1, len(json.loads((a.source / 'catalogue.json').read_text()).get('groups', [])))
        idle = 0
        for _ in range(1000):
            before = len(json.loads(state.read_text()).get('ready', {})) if state.exists() else 0
            result = publish_cycle(a.source, state, api, a.batch_size)
            after = len(result.get('ready', {}))
            print(json.dumps({'dry_run': True, 'ready_details': after}), flush=True)
            idle = idle + 1 if after == before else 0
            if idle >= 2 * groups:  # every group seen in both priority orders with nothing new
                break
        return
    if not a.token_file:
        p.error('--token-file required for a live upload')
    install(a.receipts.resolve())
    from huggingface_hub import HfApi
    api = HfApi(token=a.token_file.read_text().strip())
    with a.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result = publish_cycle(a.source, a.state, api, a.batch_size)
                print(json.dumps({'checked_unix': result.get('checked_unix'),
                                  'ready_details': len(result.get('ready', {})),
                                  'last_commit': result.get('last_commit'),
                                  'cycle': result.get('last_cycle')}), flush=True)
            except Exception as error:
                print(type(error).__name__, str(error)[:300], flush=True)
            if a.once:
                break
            time.sleep(a.interval)


if __name__ == '__main__':
    main()
