"""Publish small dashboard snapshots separately from video and trace uploads.

The gh-pages branch contains HTML, CSS, JS and progress metadata. Videos,
reference frames, masks, evaluator scores and private traces stay outside Git.
"""
import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlparse

SHELLS = ('index.html', 'pixelworld-index.html', 'analyses.html',
          'physics_iq_baseline.html', 'physics_iq_resolution.html',
          'physics_iq_512w24fps.html', 'robojudge.html', 'pcve_rigid.html',
          'phygenbench.html', 'phygen_pixelspace.html', 'formal.js', 'formal.css',
          'experiment_plan.json')


def snapshot(source, checkout, live_base):
    if urlparse(live_base).scheme != 'https':
        raise ValueError('Use a public HTTPS live dashboard URL')
    branch = subprocess.check_output(['git', 'branch', '--show-current'],
                                    cwd=checkout, text=True).strip()
    if branch != 'gh-pages':
        raise ValueError('Use a dedicated gh-pages checkout')
    # Earlier publishing accidentally committed host symlinks. Remove links
    # themselves; never follow them or delete their original data.
    for path in checkout.iterdir():
        if path.is_symlink():
            path.unlink()
    files = {name: (source / name).read_text() for name in SHELLS}
    files['.nojekyll'] = ''
    files['catalogue.json'] = (source / 'catalogue.json').read_text()
    catalogue = json.loads(files['catalogue.json'])
    for group in catalogue.get('groups', []):
        relative = Path(group['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.name != 'index.json':
            raise ValueError('Invalid public progress path')
        files[relative.as_posix()] = (source / relative).read_text()
    for name, content in files.items():
        if name.endswith('.html'):
            content = re.sub(r'<body\b', '<body data-live-base="' + live_base.rstrip('/') + '/"',
                             content, count=1)
        if re.search(r'/mnt/|/home/tiger/|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}', content):
            raise ValueError('Private data in ' + name)
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != content:
            target.write_text(content)
    subprocess.run(['git', 'add', '--all', '--', '.'], cwd=checkout, check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=checkout).returncode:
        subprocess.run(['git', 'commit', '-m', 'Refresh standalone PixelWorld dashboard and progress'],
                       cwd=checkout, check=True)
    subprocess.run(['git', 'push', 'origin', 'gh-pages'], cwd=checkout, check=True, timeout=90)
    return {'updated_utc': catalogue.get('updated_utc'), 'groups': len(catalogue.get('groups', []))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--checkout', required=True, type=Path)
    parser.add_argument('--live-base', required=True)
    parser.add_argument('--interval', type=int, default=120)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    with args.checkout.with_name(args.checkout.name + '.snapshot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                print(json.dumps(snapshot(args.source, args.checkout, args.live_base)), flush=True)
            except Exception as exc:
                print(json.dumps({'error': str(exc)}), flush=True)
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(args.interval)
