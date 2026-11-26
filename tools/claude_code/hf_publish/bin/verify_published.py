"""Read back every uploaded file from HF and only then mark a case attempt 'published'.

Inputs (all local, read-only):
  state/upload_receipts.jsonl  per-file size/sha256/git_sha1 + commit oid (wrapper)
  state/hf_incremental.json    upstream ledger: live detail relative -> commit
Output:
  state/published_ledger.json  {detail_relative: {status: published|mismatch|missing, ...}}

A record is 'published' only if, for its commit, EVERY file of that commit that
belongs to the case (detail JSON, preview/poster media, trace archive) exists at
that revision with identical size and identical sha256 (LFS/xet) or git blob
sha1 (regular files).  We also confirm the file is still identical on `main`
(a later commit could have replaced it).  Network use is read-only
(get_paths_info; hf_hub_download only as a fallback hash source).
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

DATASET = 'HuggingFriends/PixelWorld'


def load_receipts(path):
    commits = {}
    if not path.is_file():
        return commits
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('status') == 'committed' and row.get('commit'):
            commits[row['commit']] = {f['path_in_repo']: f for f in row['files']}
    return commits


def remote_info(api, paths, revision):
    out = {}
    for start in range(0, len(paths), 50):
        for item in api.get_paths_info(DATASET, paths[start:start + 50], repo_type='dataset',
                                       revision=revision, expand=False):
            out[item.path] = item
    return out


def matches(api, expected, item, revision):
    if item is None or getattr(item, 'size', None) != expected['size']:
        return False, 'size'
    lfs = getattr(item, 'lfs', None)
    sha = (lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)) if lfs else None
    if sha:
        return sha == expected['sha256'], 'lfs-sha256'
    if getattr(item, 'blob_id', None) == expected['git_sha1']:
        return True, 'git-sha1'
    # Fallback (e.g. xet-only metadata): download at the exact revision and hash.
    from huggingface_hub import hf_hub_download
    local = hf_hub_download(DATASET, item.path, repo_type='dataset', revision=revision, token=api.token)
    return hashlib.sha256(Path(local).read_bytes()).hexdigest() == expected['sha256'], 'download-sha256'


def local_run(roots, relative):
    suite, model, track, name = Path(relative).parts
    root = roots.get(suite)
    return str((root / model / track / Path(name).stem).resolve()) if root else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, required=True)
    p.add_argument('--token-file', type=Path)
    p.add_argument('--bundle', type=Path, help='bundle with live_sources.json, to record the local run dir')
    p.add_argument('--recheck', action='store_true', help='re-verify records already published')
    a = p.parse_args()
    from huggingface_hub import HfApi
    api = HfApi(token=a.token_file.read_text().strip() if a.token_file else None)
    ledger_path = a.state_dir / 'published_ledger.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    upstream = json.loads((a.state_dir / 'hf_incremental.json').read_text()).get('ready', {})
    receipts = load_receipts(a.state_dir / 'upload_receipts.jsonl')
    roots = {}
    if a.bundle and (a.bundle / 'live_sources.json').is_file():
        roots = {r['key']: Path(r['runs_root']) for r in
                 json.loads((a.bundle / 'live_sources.json').read_text())['sources']}
    summary = {'published': 0, 'pending': 0, 'mismatch': 0, 'skipped': 0}
    main_cache = {}
    for relative, entry in sorted(upstream.items()):
        prior = ledger.get(relative, {})
        if (not a.recheck and prior.get('status') == 'published'
                and prior.get('commit') == entry.get('commit')):
            summary['skipped'] += 1
            continue
        files = receipts.get(entry.get('commit'))
        if not files:
            ledger[relative] = {'status': 'no-receipt', 'commit': entry.get('commit')}
            summary['pending'] += 1
            continue
        detail = 'live/' + relative
        trace = 'live/' + str(Path(relative).with_suffix('.trace.tar.gz'))
        wanted = [detail] + ([trace] if entry.get('trace_ready') else [])
        # Media referenced by the detail JSON *as uploaded* (read back at the commit).
        from huggingface_hub import hf_hub_download
        try:
            uploaded = json.loads(Path(hf_hub_download(DATASET, detail, repo_type='dataset',
                                  revision=entry['commit'], token=api.token)).read_text())
        except Exception as error:
            ledger[relative] = {'status': 'missing', 'commit': entry['commit'], 'error': type(error).__name__}
            summary['mismatch'] += 1
            continue
        base = 'https://huggingface.co/datasets/' + DATASET + '/resolve/main/'
        for cand in uploaded.get('candidates', []):
            for field in ('preview_mp4', 'poster'):
                url = cand.get(field) or ''
                if url.startswith(base):
                    wanted.append(url[len(base):])
        missing_receipt = [w for w in wanted if w not in files]
        wanted = [w for w in wanted if w in files]
        at_commit = remote_info(api, wanted, entry['commit'])
        if 'main' not in main_cache:
            main_cache['main'] = api.dataset_info(DATASET).sha
        at_main = remote_info(api, wanted, main_cache['main'])
        checks = []
        for path in wanted:
            ok_commit, how = matches(api, files[path], at_commit.get(path), entry['commit'])
            ok_main, _ = matches(api, files[path], at_main.get(path), main_cache['main'])
            checks.append({'path': path, 'size': files[path]['size'], 'sha256': files[path]['sha256'],
                           'at_commit': ok_commit, 'on_main': ok_main, 'method': how})
        good = bool(checks) and all(c['at_commit'] and c['on_main'] for c in checks) and not missing_receipt \
            and entry.get('media_files', 0) == sum(1 for c in checks if not c['path'].startswith('live/' + relative))
        ledger[relative] = {'status': 'published' if good else 'mismatch', 'commit': entry['commit'],
                            'main_revision': main_cache['main'], 'verified_unix': time.time(),
                            'files': checks, 'missing_receipt': missing_receipt,
                            'source_sha256': entry.get('source_sha256'),
                            'local_run': local_run(roots, relative)}
        summary['published' if good else 'mismatch'] += 1
    tmp = ledger_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(ledger, indent=1)); tmp.replace(ledger_path)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
