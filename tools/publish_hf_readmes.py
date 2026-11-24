"""Publish tracked dataset/Space documentation and verify the exact commits."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from pixelworld.util import atomic_json, utc_now

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = re.compile(r'/mnt/|/home/tiger/|127\.0\.0\.1|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('dataset', 'space', 'both'), default='both')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--state', type=Path, default=ROOT/'runs/publication_state/hf_readmes.json')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    specs = [
        ('dataset', 'HuggingFriends/PixelWorld', {
            'README.md': ROOT/'docs/HF_DATASET_README.md',
            'metadata/protocol.json': ROOT/'configs/hf-publication-protocol.json'}),
        ('space', 'HuggingFriends/PixelWorld-dashboard', {
            'README.md': ROOT/'docs/HF_SPACE_README.md'})]
    bodies = []
    for kind, repo, sources in specs:
        if args.target not in ('both', kind):
            continue
        files = {name: path.read_bytes() for name, path in sources.items()}
        for name, body in files.items():
            text = body.decode()
            if PRIVATE.search(text):
                raise ValueError('Private content in documentation payload: '+name)
            if name.endswith('.json'):
                json.loads(text)
        if kind == 'space' and 'sdk: static\n' not in files['README.md'].decode():
            raise ValueError('Preserve the existing static Space SDK')
        bodies.append((kind, repo, files))
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'repositories': [repo for _,repo,_ in bodies],
                          'files': [name for _,_,files in bodies for name in files]}))
        return

    from huggingface_hub import HfApi, CommitOperationAdd, hf_hub_download
    from tools.hf_commit_budget import commit
    api = HfApi(token=args.token_file.read_text().strip() if args.token_file else None)
    receipts = []
    for kind, repo, files in bodies:
        info = api.repo_info(repo, repo_type=kind)
        if info.private:
            raise ValueError('Expected the existing public documentation repository')
        operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=body)
                      for name,body in files.items()]
        options = dict(repo_id=repo, repo_type=kind, operations=operations,
                       commit_message='Align PixelWorld documentation with the current main and analysis suite')
        result = commit(api, **options) if kind == 'dataset' else api.create_commit(**options)
        hashes = {}
        for name, body in files.items():
            local = hf_hub_download(repo, name, repo_type=kind, revision=result.oid,
                                    token=api.token, cache_dir=ROOT/'runs/publication_state/hf_readme_readback')
            if Path(local).read_bytes() != body:
                raise ValueError('Documentation readback mismatch: '+name)
            hashes[name] = hashlib.sha256(body).hexdigest()
        receipts.append({'repo':repo, 'repo_type':kind, 'commit':result.oid,
                         'verified_files':hashes,'verified_utc':utc_now()})
        atomic_json(args.state, {'status':'complete' if len(receipts)==len(bodies) else 'publishing',
                                 'receipts':receipts})
        print(json.dumps(receipts[-1]), flush=True)


if __name__ == '__main__':
    main()
