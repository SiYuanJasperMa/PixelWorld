"""Opt-in bounded private archival of closed registered attempts, with no pruning.

Supply explicit configs or JSON launch/dispatch/queue registries. The only run
discovery is registered runs_root/model/track/case directories. Proxy sources
are selected by the manifest's exact provider URL and only known response IDs
are read by the existing verified archive API. No simulations are launched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

from pixelworld.run_archive import SAFE, archive_closed_attempt, latest_archive_receipt
from pixelworld.util import atomic_json, sha256, utc_now

SINCE = '2026-11-24T09:20:00'


def registered_configs(configs=(), registries=(), overrides=None) -> dict[Path, str]:
    """Use only explicitly named config paths; do not search the filesystem."""
    scopes = {}
    overrides = {Path(k).resolve(): v for k, v in (overrides or {}).items()}

    def add(path, scope=None, relative_to=Path.cwd()):
        path = Path(path)
        if not path.is_absolute():
            path = relative_to / path
        path = path.resolve(strict=True)
        value = overrides.get(path, scope or path.stem)
        if not SAFE.fullmatch(value) or value in ('.', '..'):
            raise ValueError('Unsafe archive scope')
        if path in scopes and scopes[path] != value:
            raise ValueError(f'Conflicting archive scopes for {path}; use --scope')
        scopes[path] = value

    def visit(value, source, label=None):
        if isinstance(value, list):
            for row in value:
                visit(row, source)
        elif isinstance(value, dict):
            if value.get('full_supervisor_started') is False:
                return
            if isinstance(value.get('config'), str):
                add(value['config'], value.get('archive_scope') or value.get('name'), source.parent)
            command = value.get('command')
            if isinstance(command, list) and 'pixelworld.cli' in command and 'matrix' in command and 'run' in command:
                for i, part in enumerate(command[:-1]):
                    if part == '--config':
                        add(command[i + 1], value.get('archive_scope') or label, source.parent)
            for key, row in value.items():
                if key != 'command' and isinstance(row, (dict, list)):
                    visit(row, source, key)

    for config in configs:
        add(config)
    for registry in registries:
        registry = Path(registry).resolve(strict=True)
        visit(json.loads(registry.read_text()), registry)
    return scopes


def discover_registered(configs: dict[Path, str]):
    """Reject cross-case/model/track or changed protocol manifests."""
    seen = set()
    for config_path, scope in sorted(configs.items()):
        config = json.loads(config_path.read_text())
        root = Path(config['runs_root'])
        if not root.is_absolute():
            root = config_path.parent / root
        root = root.resolve()
        cases = {str(case) for case in config['case_ids']}
        for model in config['models']:
            model_key = model['key']
            if not SAFE.fullmatch(model_key) or model_key in ('.', '..'):
                raise ValueError('Unsafe model key')
            for track in model['tracks']:
                if track not in ('text', 'vision-input', 'vision-agent'):
                    raise ValueError('Unknown registered track')
                leaf = root / model_key / track
                leaf = leaf.resolve()
                # Fixed-depth directory enumeration; never enter workspace/frames.
                for path in sorted(leaf.glob('case_*/run_manifest.json')):
                    # Registered cloud history aliases preserve their original
                    # attempt paths; archive the resolved source, never rewrite it.
                    path = path.resolve()
                    if path in seen:
                        continue
                    seen.add(path)
                    try:
                        manifest = json.loads(path.read_text())
                        attempt = manifest.get('attempt')
                        case = str(manifest.get('case_id'))
                        identity = (manifest.get('model_key'), manifest.get('track'), manifest.get('protocol'))
                        if (identity != (model_key, track, config['protocol']) or case not in cases or
                                type(attempt) is not int or attempt < 1 or
                                path.parent.name != f'case_{case}_attempt_{attempt:02d}'):
                            continue
                    except (OSError, ValueError, TypeError):
                        continue
                    yield path, manifest, scope, config_path


def _existing(root: Path, scope: str, path: Path, manifest: dict, *, proxy=None, request_ids=(), extra_files=None, proxy_log_fallbacks=()) -> str | None:
    parent = root / scope / manifest['model_key']
    index = parent / 'archive_index.json'
    if not index.is_file():
        return None
    key = f"{manifest['track']}/{manifest['case_id']}/{manifest['attempt']}"
    number = json.loads(index.read_text()).get(key)
    if number is None:
        return None
    receipt = parent / f'run_{number:04d}/verified.json'
    if not receipt.is_file():
        return None
    old = json.loads(receipt.read_text())
    target = receipt.parent / 'artifacts.tar.gz'
    if (not target.is_file() or target.is_symlink() or
            target.stat().st_size != old.get('archive_bytes')):
        return 'existing_archive_missing_or_incomplete'
    if latest_archive_receipt(path.parent,root,benchmark=scope,proxy_logs=proxy,
                              request_ids=request_ids,extra_files=extra_files,proxy_log_fallbacks=proxy_log_fallbacks) is not None:
        return 'already_verified'
    return None  # Capture a new numbered revision; the old receipt remains intact.


def archive_recent(configs: dict[Path, str], root: Path, proxy_roots: dict[str, Path], *,
                   since=SINCE, apply=False, limit=4, proxy_indexes=None, evaluation_records=(), prune=False, proxy_fallbacks=None) -> dict:
    if limit < 1:
        raise ValueError('Archive limit must be positive')
    root = root.absolute()
    counts, items, attempts, scope_scan_complete = Counter(), [], 0, True
    for path, manifest, scope, config in discover_registered(configs):
        if attempts >= limit:
            counts['scan_limit_reached'] += 1
            scope_scan_complete = False
            break
        if manifest.get('status') not in ('complete', 'failed'):
            counts['active_or_unknown'] += 1
            continue
        closed_utc = manifest.get('completed_utc') or manifest.get('started_utc') or ''
        if closed_utc < since:
            counts['before_window'] += 1
            continue
        provider = (manifest.get('provider') or {}).get('base_url')
        proxy = proxy_roots.get(provider)
        fallbacks=(proxy_fallbacks or {}).get(provider,())
        request_ids=[];wire_evidence={}
        index=(proxy_indexes or {}).get(provider)
        if index:
            from tools.archive_proxy_request_index import request_ids_for_run
            wire_evidence=request_ids_for_run(index,manifest,run_path=path.parent)
            request_ids=wire_evidence['request_ids']
        extras={'configuration/registry_config.json':config}
        for record in evaluation_records:
            recorded_run=record.get('source_run') or record.get('run')
            if recorded_run and Path(recorded_run).resolve() == path.parent.resolve() and record.get('source_manifest_sha256') == sha256(path):
                score=Path(record.get('score_file') or record.get('path'))
                expected=record.get('score_sha256')
                if score.is_file() and (not expected or sha256(score)==expected):
                    protocol=record.get('protocol','recorded-score')
                    name=record.get('archive_name') or f'evaluations/{protocol}/{score.stem}.json'
                    extras[name]=score
        if proxy is None:
            counts['wire_route_unavailable_artifacts_retained'] += 1
        try:
            existing = _existing(root, scope, path, manifest,proxy=proxy,request_ids=request_ids,extra_files=extras,proxy_log_fallbacks=fallbacks)
        except (OSError, ValueError, KeyError, TypeError):
            counts['existing_archive_unreadable'] += 1
            continue
        pending_prune=prune and existing=='already_verified' and any(
            (path.parent/'workspace'/name).is_file() for name in ('pixels.packing.npy','pixels.npz.partial'))
        if existing and not pending_prune:
            counts[existing] += 1
            continue
        attempts += 1
        item = {'source_run': str(path.parent), 'config': str(config), 'scope': scope,
                'provider_base_url': provider, 'source_manifest_sha256': sha256(path),
                'closed_utc': closed_utc,
                'prune_reproducible': prune, 'status': 'eligible', 'wire_evidence':wire_evidence}
        if apply:
            try:
                receipt = archive_closed_attempt(path.parent, root, benchmark=scope,
                                                 proxy_logs=proxy, prune_reproducible=prune,
                                                 request_ids=request_ids,extra_files=extras,proxy_log_fallbacks=fallbacks)
                item.update(status='verified', run_number=receipt['run_number'],
                            archive_bytes=receipt['archive_bytes'], wire_pair_count=receipt['wire_pair_count'],
                            known_requests=len(receipt['known_request_roles']),
                            pruned_source_files=receipt['pruned_source_files'])
            except (OSError, ValueError, KeyError) as error:
                item.update(status='failed', error=str(error), source_preserved=True)
        counts[item['status']] += 1
        items.append(item)
    return {'schema': 'pixelworld-registered-private-backfill-v1', 'updated_utc': utc_now(),
            'since': since, 'apply': apply, 'archive_root': str(root), 'limit': limit,
            'registered_configs': len(configs), 'counts': dict(counts), 'items': items,
            'registered_scope_scan_complete': scope_scan_complete,
            'source_pruning_enabled': prune}


def _assignments(values):
    result = {}
    for value in values:
        key, separator, target = value.partition('=')
        if not separator or not key or not target:
            raise ValueError('Expected NAME=VALUE assignment')
        result[key] = target
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, action='append', default=[])
    p.add_argument('--registry', type=Path, action='append', default=[])
    p.add_argument('--scope', action='append', default=[], metavar='CONFIG=BENCHMARK_CONDITION')
    p.add_argument('--proxy-route', action='append', default=[], metavar='BASE_URL=PRIVATE_LOG_ROOT')
    p.add_argument('--proxy-fallback', action='append', default=[], metavar='BASE_URL=RETAINED_LOG_ROOT')
    p.add_argument('--proxy-index', action='append', default=[], metavar='BASE_URL=PRIVATE_INDEX_DB')
    p.add_argument('--evaluation-registry', type=Path, action='append', default=[])
    p.add_argument('--prune-reproducible', action='store_true')
    p.add_argument('--archive-root', type=Path, default=os.environ.get('PIXELWORLD_VERIFIED_ARCHIVE_ROOT'))
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--since', default=SINCE)
    p.add_argument('--limit', type=int, default=4)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--watch', action='store_true')
    p.add_argument('--poll-seconds', type=float, default=60)
    p.add_argument('--lock', type=Path)
    args = p.parse_args()
    if not args.archive_root or not (args.config or args.registry):
        p.error('Explicit archive root and registered configs/registries are required')
    if args.limit < 1 or not math.isfinite(args.poll_seconds) or args.poll_seconds < 1:
        p.error('Use positive limit and a finite poll interval of at least one second')
    os.umask(0o077)
    configs = registered_configs(args.config, args.registry, _assignments(args.scope))
    routes = {url: Path(root).resolve(strict=True) for url, root in _assignments(args.proxy_route).items()}
    indexes={url:Path(path) for url,path in _assignments(args.proxy_index).items()}
    fallbacks={}
    for value in args.proxy_fallback:
        url, separator, path=value.partition('=')
        if not separator or url not in routes or not path:p.error('Fallback requires an existing exact proxy route')
        fallbacks.setdefault(url,[]).append(Path(path).resolve(strict=True))
    if not routes:
        p.error('At least one exact --proxy-route must be authorized')
    lock_path = args.lock or args.report.with_suffix('.lock')
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = {'pid': os.getpid(), 'pid_start_marker': Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(') ', 1)[1].split()[19],
                'command': sys.argv, 'started_utc': utc_now(), 'lock': str(lock_path)}
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('An archive worker already owns this report lock')
        lock.seek(0); lock.truncate(); lock.write(json.dumps(identity) + '\n'); lock.flush()
        print(json.dumps({'worker': identity, 'ready': True}), flush=True)
        while not stopped:
            configs = registered_configs(args.config, args.registry, _assignments(args.scope))
            if indexes:
                from tools.archive_proxy_request_index import update_index
                for url,path in indexes.items():
                    if url in routes:update_index(routes[url],path)
            evaluation_records=[]
            for path in args.evaluation_registry:
                if path.is_file():
                    records=json.loads(path.read_text())
                    evaluation_records.extend(records if isinstance(records,list) else records.get('records',records.get('rows',[])))
            report = archive_recent(configs, args.archive_root, routes, since=args.since,
                                    apply=args.apply, limit=args.limit,proxy_indexes=indexes,
                                    evaluation_records=evaluation_records,prune=args.prune_reproducible,proxy_fallbacks=fallbacks)
            report['worker'] = identity
            atomic_json(args.report, report)
            print(json.dumps({'updated_utc': report['updated_utc'], 'counts': report['counts']}), flush=True)
            if not args.watch:
                break
            # Sleep ends on the next second after SIGTERM; lock releases on exit.
            deadline = time.monotonic() + args.poll_seconds
            while not stopped and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))


if __name__ == '__main__':
    main()
