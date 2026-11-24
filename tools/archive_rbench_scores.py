"""Attach official RBench scores to exact verified generation-run revisions."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

from pixelworld.util import atomic_json, sha256
from tools.evaluate_rbench_common_gpt61 import GPT61_JUDGE
from tools.watch_rbench_common_scores import PROTOCOL


def index_receipts(root):
    rows = {}
    for path in sorted(root.glob('*/*/run_*/verified.json')):
        if path.is_symlink() or path.resolve() != path:
            continue
        r = json.loads(path.read_text())
        k = (r['source_run'], r['members']['run_manifest.json'])
        rows.setdefault(k, []).append((path, r))
    # Wire/config revisions may legitimately share a generation manifest.
    # Keep old attached scores in place and target the newest verified copy.
    return {key: [max(values, key=lambda item: (item[1].get('completed_utc', ''), item[1]['run_number']))]
            for key, values in rows.items()}


def attach(source, lookup):
    raw = source.read_bytes()
    row = json.loads(raw)
    if (row.get('judge'), row.get('protocol')) != (GPT61_JUDGE, PROTOCOL):
        raise ValueError('Unexpected RBench evaluation protocol or judge')
    key = (row['source_run'], row['source_manifest_sha256'])
    archives = lookup.get(key, [])
    if len(archives) != 1:
        return {'status': 'no_unique_exact_archive', 'model': row['model'], 'case': row['case']}
    archive, generation = archives[0]
    if (row['model'], row['case'], row['generation_attempt']) != (
        generation['model_key'], generation['case_id'], generation['attempt'],
    ):
        raise ValueError('Evaluation identity does not match the generation archive')
    if generation.get('verification') != 'independent SHA256 member readback':
        raise ValueError('Generation archive has no verified readback')
    if archive.resolve() != archive or archive.parent.is_symlink():
        raise ValueError('Unsafe generation receipt path')
    dst = archive.parent/'evaluations'/PROTOCOL
    if dst.resolve() != dst:
        raise ValueError('Evaluation directory points outside the archive')
    dst.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    info = {'status': 'verified', 'model': row['model'], 'case': row['case'],
            'generation_run_number': generation['run_number'], 'generation_manifest_sha256': key[1],
            'judge': GPT61_JUDGE, 'protocol': PROTOCOL, 'score_sha256': digest,
            'evidence_scope': 'exact score row only; no inferred linkage to judge API originals'}
    with (dst/'.attach_lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = dst/'result.json'
        if target.is_symlink() or (dst/'verified.json').is_symlink():
            raise ValueError('Unsafe evaluation attachment target')
        if target.is_file():
            if sha256(target) != digest:
                raise ValueError('Existing attachment contains a different score record')
        else:
            tmp = dst/'result.part'
            fd = os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if sha256(tmp) != digest:
                    raise ValueError('Evaluation attachment failed SHA256 readback')
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
        atomic_json(dst/'verified.json', info)
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scores', type=Path, required=True)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    if not a.scores.is_dir() or not a.archive.is_dir():
        p.error('Expected existing score and archive directories')
    os.umask(0o077)
    receipts = index_receipts(a.archive.resolve())
    results = [attach(file, receipts) for file in sorted(a.scores.glob('*/results/[0-9][0-9][0-9][0-9].json'))
               if not file.is_symlink() and file.resolve() == file.absolute()]
    atomic_json(a.report, {'verified': sum(row['status'] == 'verified' for row in results), 'rows': results})
    print(json.dumps({'verified':sum(row['status']=='verified' for row in results), 'seen':len(results)}))


if __name__ == '__main__':
    main()
