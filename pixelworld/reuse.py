"""Resolve audited historical deliveries without copying or rewriting a run."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path


@lru_cache(maxsize=32)
def _registry(path: str, stamp: int, size: int) -> dict:
    raw = json.loads(Path(path).read_text())
    if raw.get('schema') != 'pixelworld-compatible-history-v1':
        raise ValueError('Unsupported reuse registry')
    index = {}
    for item in raw['entries']:
        key = (item['model_key'], item['track'], item['case_id'])
        if key in index:
            raise ValueError('Duplicate historical delivery')
        index[key] = item
    return index


def registry(root: Path) -> dict:
    path = root / 'reuse_registry.json'
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    return _registry(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4096)
def _manifest(path: str, stamp: int, size: int, expected_hash: str) -> dict:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError('Historical manifest changed after reuse audit: ' + path)
    manifest = json.loads(data)
    if manifest.get('status') != 'complete' or not manifest.get('validation', {}).get('pass'):
        raise ValueError('Reused delivery is no longer valid: ' + path)
    return manifest


def delivery(root: Path, model: str, track: str, case: str):
    item = registry(root).get((model, track, case))
    if item is None:
        return None
    path = Path(item['source']) / 'run_manifest.json'
    stat = path.stat()
    manifest = _manifest(str(path), stat.st_mtime_ns, stat.st_size, item['manifest_sha256'])
    return item, manifest


def run_sources(folder: Path, case: str | None = None):
    """Public cache names distinguish old and new runs with the same attempt ID."""
    paths = folder.glob(f'case_{case}_attempt_*' if case else 'case_*_attempt_*')
    rows = [(run, run.name, None) for run in paths]
    root = folder.parent.parent
    model, track = folder.parent.name, folder.name
    for (key, condition, cid), item in registry(root).items():
        if key != model or condition != track or (case is not None and cid != case):
            continue
        delivery(root, model, track, cid)  # Fail closed on altered audit evidence.
        run = Path(item['source'])
        identity = hashlib.sha256(str(run).encode()).hexdigest()[:16]
        rows.append((run, 'history_' + identity + '__' + run.name, item))
    return rows


def public_provenance(item: dict) -> dict:
    return {key: item.get(key) for key in (
        'source_suite', 'original_attempt', 'original_model', 'original_protocol',
        'original_filesystem_policy', 'original_output_token_budget',
        'model_attempts_to_delivery', 'manifest_sha256')}
