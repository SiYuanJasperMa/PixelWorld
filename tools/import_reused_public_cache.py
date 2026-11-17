"""Import already-sanitized public details for audited historical deliveries.

This never reads or writes generation workspaces beyond the immutable manifest
stamp already authorized by ``reuse_registry.json``. Video assets are referenced
only when the destination Git tree already tracks their content-addressed paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from pixelworld.reuse import public_provenance
from tools.live_benchmark_data import referenced_assets


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
    temporary.replace(path)


def import_cache(bundle: Path, old_checkout: Path, new_checkout: Path) -> dict:
    old_public = old_checkout / 'pixelworld'
    new_public = new_checkout / 'pixelworld'
    old_cache = read(old_checkout / '.publisher_cache.json')
    new_cache_path = new_checkout / '.publisher_cache.json'
    new_cache = read(new_cache_path)
    tracked = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', 'pixelworld/assets'], cwd=new_checkout
    ).decode().split('\0')
    tracked_assets = {name.removeprefix('pixelworld/') for name in tracked if name}
    imported = skipped = 0
    missing_assets = []
    pending = []
    for registry_path in sorted((bundle / 'formal_generations').glob('*/reuse_registry.json')):
        registry = read(registry_path)
        for item in registry.get('entries', []):
            suite = item['source_suite']
            identity = f"{suite}/{item['model_key']}/{item['track']}"
            run_name = Path(item['source']).name
            cached = old_cache.get(identity, {}).get(run_name)
            if not cached:
                skipped += 1
                continue
            source_detail = old_public / cached['detail']
            detail = read(source_detail)
            if (detail.get('case_id') != item['case_id'] or detail.get('delivery_status') != 'complete'
                    or detail.get('model_key') != item['model_key']):
                skipped += 1
                continue
            absent = sorted(referenced_assets(detail) - tracked_assets)
            if absent:
                missing_assets.append({'case_id': item['case_id'], 'assets': absent})
                skipped += 1
                continue
            source_identity = hashlib.sha256(item['source'].encode()).hexdigest()[:16]
            cache_key = f"history_{source_identity}__{run_name}"
            relative = f"{identity}/{cache_key}.json"
            detail['reused'] = True
            detail['provenance'] = public_provenance(item)
            record = dict(cached)
            record.update(detail=relative, reused=True)
            pending.append((identity, cache_key, relative, detail, record))
    for identity, cache_key, relative, detail, record in pending:
        write(new_public / relative, detail)
        new_cache.setdefault(identity, {})[cache_key] = record
        imported += 1
    write(new_cache_path, new_cache)
    return {'imported': imported, 'skipped_without_public_cache': skipped,
            'skipped_untracked_assets': len(missing_assets),
            'destination_cache_groups': len(new_cache), 'assets_reencoded': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--old-checkout', type=Path, required=True)
    parser.add_argument('--new-checkout', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = import_cache(args.bundle.resolve(), args.old_checkout.resolve(), args.new_checkout.resolve())
    if args.report:
        write(args.report, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
