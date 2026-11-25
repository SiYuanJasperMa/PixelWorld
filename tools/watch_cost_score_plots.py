"""Refresh and publish measured plots when their source snapshots change."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import time

from tools.build_cost_score_plots import build


def main():
    from huggingface_hub import HfApi, CommitOperationAdd
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-root', type=Path, required=True)
    parser.add_argument('--main-status', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=60)
    args = parser.parse_args()
    if args.interval < 60:
        parser.error('Publish no more than once per minute')
    api = HfApi(token=args.token_file.read_text().strip())
    previous = None
    while True:
        try:
            paths = [args.web_root / 'cost_effect_progress.json', args.main_status]
            digest = hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()
            if digest != previous:
                proof = build(args.web_root, args.main_status)
                files = [args.web_root / 'cost_score_plots.html', *sorted((args.web_root / 'cost_score_charts').glob('*'))]
                operations = []
                for path in files:
                    body = path.read_bytes()
                    if path.suffix in ('.json', '.html', '.svg') and any(x in body for x in (b'/mnt/', b'/home/tiger/', b'hf_', b'ghp_')):
                        raise ValueError('Private data in chart publication')
                    operations.append(CommitOperationAdd(path_in_repo=path.relative_to(args.web_root).as_posix(), path_or_fileobj=body))
                receipt = api.create_commit('HuggingFriends/PixelWorld-dashboard', repo_type='space',
                    operations=operations, commit_message='Refresh measured cost-score plots and analysis settings')
                api.create_commit('HuggingFriends/PixelWorld', repo_type='dataset', operations=[
                    CommitOperationAdd(path_in_repo='dashboard/' + p.relative_to(args.web_root).as_posix(), path_or_fileobj=p.read_bytes()) for p in files],
                    commit_message='Archive measured cost-score plot snapshot')
                previous = digest
                print({'commit': receipt.oid, 'points': len(proof['rows'])}, flush=True)
        except Exception as exc:
            print({'error': type(exc).__name__}, flush=True)
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
