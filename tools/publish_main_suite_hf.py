"""Publish only the revised main table and its public-safe progress snapshot."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import urlopen

from pixelworld.util import atomic_json, utc_now
from tools.build_main_suite_status import build, render

SPACE = 'HuggingFriends/PixelWorld-dashboard'
PROTOCOL = 'pixelworld-main-suite-20261124'
PRIVATE = re.compile(r'/mnt/|/home/tiger/|127\.0\.0\.1|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}')


def payload(status):
    if status.get('protocol') != PROTOCOL:
        raise ValueError('Refusing to upload the wrong suite')
    files = {'index.html': render(status),
             'main_suite_status_v2.json': json.dumps(status, ensure_ascii=False, indent=2)}
    if any(PRIVATE.search(body) for body in files.values()):
        raise ValueError('Dashboard snapshot contains private data')
    stable = {key: val for key, val in status.items() if key != 'updated_utc'}
    digest = hashlib.sha256((render({**stable, 'updated_utc': ''}) + json.dumps(stable, sort_keys=True)).encode()).hexdigest()
    return files, digest


def read_public(revision, path):
    url = f'https://huggingface.co/spaces/{SPACE}/resolve/{revision}/{path}'
    with urlopen(url, timeout=12) as reply:
        return reply.read(1024 * 1024).decode()


def publish(api, status, state):
    from huggingface_hub import CommitOperationAdd

    files, digest = payload(status)
    info = api.space_info(SPACE, timeout=12)
    if info.private or not info.sha:
        raise ValueError('Expected the existing public PixelWorld Space')
    prior = json.loads(state.read_text()) if state.exists() else {}
    if (prior.get('digest'), prior.get('commit')) == (digest, info.sha):
        return prior
    remote = read_public(info.sha, 'index.html')
    is_new = f'content="{PROTOCOL}"' in remote
    if not is_new:
        legacy = read_public(info.sha, 'pixelworld-index.html')
        if not all('formal.js' in page and 'PixelWorld' in page for page in (remote, legacy)):
            raise ValueError('The remote homepage or archived homepage is not the expected PixelWorld frontend')
    result = api.create_commit(SPACE, repo_type='space', parent_commit=info.sha,
        operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=body.encode())
                    for name, body in files.items()],
        commit_message='Refresh PixelWorld main suite progress')
    proof = {'commit': result.oid, 'digest': digest, 'published_utc': utc_now(), 'protocol': PROTOCOL}
    atomic_json(state, proof)
    return proof


def main():
    from huggingface_hub import HfApi

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nightly', type=Path, required=True)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--watch-seconds', type=int)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.watch_seconds is not None and args.watch_seconds < 300:
        parser.error('Use at least five minutes between publication checks')
    state = args.state
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                status = build(args.nightly, args.publication)
                if args.dry_run:
                    files, digest = payload(status)
                    print(json.dumps({'files': sorted(files), 'digest': digest, 'dry_run': True}), flush=True)
                    break
                print(json.dumps(publish(HfApi(), status, state)), flush=True)
            except Exception as error:
                if args.watch_seconds is None:
                    raise
                print(json.dumps({'error_type': type(error).__name__, 'utc': utc_now()}), flush=True)
            if args.watch_seconds is None:
                break
            time.sleep(args.watch_seconds)


if __name__ == '__main__':
    main()
