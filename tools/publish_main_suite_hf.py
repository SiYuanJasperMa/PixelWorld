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
from pixelworld.publication_endpoints import canonicalize_code_links
from tools.build_main_suite_status import build, render, project_all_video_evaluation_progress

SPACE = 'HuggingFriends/PixelWorld-dashboard'
DATASET = 'HuggingFriends/PixelWorld'
PROTOCOL = 'pixelworld-main-suite-20261124'
PRIVATE = re.compile(r'/mnt/|/home/tiger/|127\.0\.0\.1|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}')
DETAIL_ALIASES = {'physics_iq_baseline.html': 'physics_iq',
                  'rbench_common_manipulation.html': 'rbench',
                  'phygenbench.html': 'phygenbench',
                  'phygen_pixelspace.html': 'phygenbench',
                  'pcve_rigid.html': 'pcve'}


def payload(status):
    if status.get('protocol') != PROTOCOL:
        raise ValueError('Refusing to upload the wrong suite')
    if 'all_video_evaluation_progress' in status:
        status = {**status, 'all_video_evaluation_progress': project_all_video_evaluation_progress(
            status['all_video_evaluation_progress'])}
    assets = Path(__file__).resolve().parents[1]/'pixelworld/assets'
    browser_script = (assets/'results_browser.js').read_text()
    browser_revision = hashlib.sha256(browser_script.encode()).hexdigest()[:12]
    browser = (assets/'results_browser.html').read_text().replace(
        'src="results_browser.js"', 'src="results_browser.js?v='+browser_revision+'"')
    files = {'index.html': render(status),
             'main_suite_status_v2.json': json.dumps(status, ensure_ascii=False, indent=2),
             'videos.html': browser,
             'results_browser.js': browser_script}
    # Keep existing shared links usable while all case pages read the current
    # HF catalogue. The model/suite/case query is preserved by using the same
    # browser in place, rather than forwarding to a stale static dashboard.
    for filename, benchmark in DETAIL_ALIASES.items():
        files[filename] = re.sub(r'<body\b', '<body data-benchmark="'+benchmark+'"', browser, count=1)
    stylesheet = assets/'results_browser.css'
    if stylesheet.exists():
        files[stylesheet.name] = stylesheet.read_text()
    files = {name: canonicalize_code_links(body) for name, body in files.items()}
    if any(PRIVATE.search(body) for body in files.values()):
        raise ValueError('Dashboard snapshot contains private data')
    stable = {key: val for key, val in status.items() if key != 'updated_utc'}
    digest = hashlib.sha256((render({**stable, 'updated_utc': ''}) + json.dumps(stable, sort_keys=True)
                             + ''.join(name+body for name,body in sorted(files.items())
                                       if name not in ('index.html','main_suite_status_v2.json'))).encode()).hexdigest()
    return files, digest


def dataset_payload(status):
    files, digest = payload(status)
    index = {
        'schema': 'pixelworld-experiment-index-v1', 'protocol': status['protocol'],
        'snapshot_utc': status['updated_utc'], 'benchmarks': ['physics_iq', 'rbench', 'phygenbench', 'pcve'],
        'models': status['models'], 'harness_progress': status.get('harness_progress', {}),
        'links': {'catalogue': 'live/catalogue.json', 'coverage': 'metadata/publication_coverage.json',
                  'dashboard': 'dashboard/index.html', 'legacy': 'data/catalogue.json',
                  'all_video_evaluation_progress': 'metadata/all_video_evaluation_progress.json'},
    }
    return {'metadata/main_suite_status.json': files['main_suite_status_v2.json'],
            'metadata/experiment_index.json': json.dumps(index, ensure_ascii=False, indent=2),
            'metadata/all_video_evaluation_progress.json': json.dumps(project_all_video_evaluation_progress(
                status.get('all_video_evaluation_progress')), ensure_ascii=False, indent=2),
            'dashboard/index.html': files['index.html']}, digest


def publish_dataset(api, status, state):
    from huggingface_hub import CommitOperationAdd, hf_hub_download
    from tools.hf_commit_budget import commit
    files, digest = dataset_payload(status)
    receipt = state.with_name(state.stem + '_dataset.json')
    prior = json.loads(receipt.read_text()) if receipt.exists() else {}
    if prior.get('digest') == digest:
        return prior
    info = api.dataset_info(DATASET, timeout=12)
    if info.private:
        raise ValueError('Expected the existing public result dataset')
    result = commit(api, repo_id=DATASET, repo_type='dataset',
        operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=body.encode()) for name, body in files.items()],
        commit_message='Refresh current experiment index and dashboard snapshot')
    # Verify the new commit rather than a cached main-branch response.
    for name, body in files.items():
        local = hf_hub_download(DATASET, name, repo_type='dataset', revision=result.oid, token=api.token)
        if Path(local).read_bytes() != body.encode():
            raise ValueError('Published dataset snapshot failed readback')
    proof = {'commit': result.oid, 'digest': digest, 'published_utc': utc_now(), 'protocol': PROTOCOL}
    atomic_json(receipt, proof)
    return proof


def read_public(revision, path):
    url = f'https://huggingface.co/spaces/{SPACE}/resolve/{revision}/{path}'
    with urlopen(url, timeout=12) as reply:
        return reply.read(1024 * 1024).decode()


def publish(api, status, state):
    from huggingface_hub import CommitOperationAdd

    dataset_proof = publish_dataset(api, status, state)
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
    proof = {'commit': result.oid, 'dataset_commit': dataset_proof['commit'], 'digest': digest, 'published_utc': utc_now(), 'protocol': PROTOCOL}
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
    if args.watch_seconds is not None and args.watch_seconds < 60:
        parser.error('Use at least one minute between publication checks')
    state = args.state
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                status = build(args.nightly, args.publication)
                if args.dry_run:
                    files, digest = payload(status)
                    print(json.dumps({'files': sorted(files), 'digest': digest, 'dry_run': True}), flush=True)
                    break
                proof = publish(HfApi(), status, state)
                atomic_json(args.nightly/'main_suite_status_v2.json', status)
                print(json.dumps(proof), flush=True)
            except Exception as error:
                if args.watch_seconds is None:
                    raise
                print(json.dumps({'error_type': type(error).__name__, 'utc': utc_now()}), flush=True)
            if args.watch_seconds is None:
                break
            time.sleep(args.watch_seconds)


if __name__ == '__main__':
    main()
