"""Read registered experiment snapshots and optionally verify local processes.

This command never starts an agent, retries a case, calls a model, uploads data,
or rewrites a generation manifest. Machine-specific paths belong in a private
runtime map, not in this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

FIELDS = ('complete', 'running', 'pending', 'exhausted')


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def local_processes(proc_root: Path = Path('/proc')) -> dict[int, list[str]]:
    result = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            state = (entry/'stat').read_text().rsplit(') ', 1)[1].split()[0]
            if state == 'Z':
                continue
            result[int(entry.name)] = [v.decode(errors='replace') for v in
                (entry/'cmdline').read_bytes().split(b'\0') if v]
        except (OSError, IndexError):
            continue
    return result


def option(arguments: list[str], name: str):
    try:
        return arguments[arguments.index(name)+1]
    except (ValueError, IndexError):
        return None


def timestamp_age(value, now: datetime):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return max(0, round((now-date).total_seconds()))
    except (AttributeError, TypeError, ValueError):
        return None


def cohort_status(entry: dict, *, role: str, processes=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    row = {key: entry[key] for key in
        ('kind', 'benchmark', 'condition', 'model_key', 'track', 'expected')}
    expected = row['expected']
    if type(expected) is not int or expected < 1:
        raise ValueError('Expected scope must be a positive integer')
    row.update(counts={key: 0 for key in FIELDS}, counts_basis='missing_snapshot',
        process_verification='not_requested', live_case_processes=None,
        dispatcher_live=None, execution_state='unknown')
    config_path = Path(entry['config']).absolute()
    try:
        config = read_object(config_path)
        ids = config.get('case_ids', [])
        model = next((m for m in config.get('models', []) if m.get('key') == row['model_key']), None)
        if (len(ids) != expected or len(set(ids)) != expected or model is None
                or row['track'] not in model.get('tracks', [])):
            raise ValueError('Registered config differs from the declared cohort')
        snapshot = read_object(Path(config['runs_root'])/'status.json')
        group = snapshot.get('groups', {}).get(f"{row['model_key']}/{row['track']}")
        if not isinstance(group, dict):
            raise ValueError('Snapshot does not contain the registered model and track')
        counts = group.get('counts', {})
        if (not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values())
                or set(counts)-set(FIELDS) or sum(counts.values()) > expected):
            raise ValueError('Invalid snapshot counters')
        row['counts'] = {key: counts.get(key, 0) for key in FIELDS}
        row['counts']['pending'] += expected-sum(row['counts'].values())
        row['counts_basis'] = 'registered_worker_snapshot'
        row['updated_utc'] = snapshot.get('updated_utc')
        row['snapshot_age_seconds'] = timestamp_age(row['updated_utc'], now)
        row['latest_delivery_utc'] = (group.get('latest_completion') or [None])[0]
        row['execution_state'] = 'snapshot_only'
        row['last_delivery_age_seconds'] = timestamp_age(row['latest_delivery_utc'], now)
        row['retry_window_exhausted'] = row['counts']['exhausted']
        row['confirmed_model_failures'] = None
        if row['counts']['complete'] == expected:
            row['generation_scope'] = 'complete_in_snapshot'
        else:
            row['generation_scope'] = 'incomplete'
        if processes is not None:
            if role != entry.get('owner_role', 'cpu'):
                row['process_verification'] = 'requires_owner_host'
            else:
                sources = {str(config_path), *entry.get('compatible_configs', [])}
                allowed_cases = set(ids)
                matching = [args for args in processes.values() if 'run-case' in args
                    and option(args, '--config') in sources
                    and option(args, '--model') == row['model_key']
                    and option(args, '--track') == row['track']
                    and option(args, '--case') in allowed_cases]
                dispatcher = processes.get(snapshot.get('supervisor_pid'), [])
                native = entry.get('controller')
                row.update(process_verification='owner_proc', live_case_processes=len(matching),
                    dispatcher_live=bool(('pixelworld.cli' in dispatcher and 'matrix' in dispatcher
                        and option(dispatcher, '--config') in sources)
                        or (native and native in dispatcher)))
                row['execution_state'] = ('case_processes_live' if matching else
                    'dispatcher_live' if row['dispatcher_live'] else
                    'no_owner_process_observed')
    except (OSError, ValueError, KeyError, TypeError) as error:
        row['counts'] = {key: expected if key == 'pending' else 0 for key in FIELDS}
        row['counts_basis'] = 'unavailable_or_invalid'
        row['error_type'] = type(error).__name__
    return row


def collect(runtime_map: dict, *, role='head', processes=None, now=None) -> dict:
    if runtime_map.get('schema') != 'pixelworld-operator-map-v1':
        raise ValueError('Unsupported runtime-map schema')
    now = now or datetime.now(timezone.utc)
    entries = runtime_map.get('cohorts', [])
    identities = [(e['kind'], e['condition'], e['model_key'], e['track']) for e in entries]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate cohort in runtime map')
    rows = [cohort_status(entry, role=role, processes=processes, now=now) for entry in entries]
    totals = {}
    for kind in ('main', 'analysis'):
        selected = [row for row in rows if row['kind'] == kind]
        counts = Counter()
        for row in selected:
            counts.update(row['counts'])
        totals[kind] = {**{key: counts[key] for key in FIELDS},
            'expected': sum(row['expected'] for row in selected),
            'live_case_processes': sum(row['live_case_processes'] or 0 for row in selected)
                if processes is not None else None}
    return {'schema': 'pixelworld-operator-status-v1', 'checked_utc': now.isoformat(),
        'hostname': socket.gethostname(), 'role': role, 'read_only': True,
        'policy': runtime_map.get('policy', {}), 'totals': totals, 'cohorts': rows,
        'note': 'Snapshot counts are historical evidence; owner processes and successful deliveries are separate. No model failure is inferred from an exhausted retry window.'}


def render_text(report: dict) -> str:
    lines = [f"PixelWorld status | {report['checked_utc']} | {report['role']} | read-only",
        'Policy: '+json.dumps(report['policy'], ensure_ascii=False)]
    for kind, values in report['totals'].items():
        lines.append(f"{kind}: {values['complete']}/{values['expected']} complete; "
            f"{values['running']} running in snapshots; {values['pending']} pending; "
            f"{values['exhausted']} retry windows exhausted")
    lines.append('kind | condition | model | complete/expected | snapshot running | live cases | snapshot age s | state')
    for row in report['cohorts']:
        count = row['counts']
        lines.append(' | '.join(map(str, [row['kind'], row['condition'], row['model_key'],
            f"{count['complete']}/{row['expected']}", count['running'],
            row['live_case_processes'], row.get('snapshot_age_seconds'), row['execution_state']])))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map', type=Path, required=True, help='Private owner-supplied runtime map')
    parser.add_argument('--role', choices=('head', 'cpu'), default='head')
    parser.add_argument('--verify-processes', action='store_true', help='Inspect /proc only on the owning host')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--output', type=Path, help='Optional status report; generation records are never changed')
    args = parser.parse_args()
    report = collect(read_object(args.map), role=args.role,
        processes=local_processes() if args.verify_processes else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))


if __name__ == '__main__':
    main()
