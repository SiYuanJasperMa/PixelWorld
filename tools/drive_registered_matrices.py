"""Run a bounded number of pre-registered PixelWorld matrix supervisors."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from pixelworld.config import load_config
from pixelworld.util import atomic_json, utc_now

IDENTITY_FIELDS = ('name', 'config', 'runs_root', 'expected')


def process_identity(pid):
    root = Path('/proc', str(pid))
    try:
        stat = (root/'stat').read_text().rsplit(') ',1)[1].split()
        if stat[0] == 'Z':
            return None
        args = [x.decode() for x in (root/'cmdline').read_bytes().split(b'\0') if x]
        return stat[19], args
    except (FileNotFoundError, ProcessLookupError):
        return None


def read_queue(groups, approved, runtime):
    registry = json.loads(approved.read_text())['configured']
    registered = {r['name']: r for r in registry}
    roots = set()
    names = set()
    queues = {}
    commands = {}
    for spec in groups:
        pending = json.loads(spec.read_text())
        state = spec.with_suffix('.status.json')
        rows = json.loads(state.read_text())
        if len(rows) != len(pending):
            raise ValueError('Queue size changed')
        for row, declared in zip(rows, pending):
            name = row['name']
            expected = registered[name]
            if name in names or any(row[k] != declared[k] or row[k] != expected[k] for k in IDENTITY_FIELDS):
                raise ValueError('Queue no longer matches the registered cases')
            if Path(name).name != name or row['state'] not in ('queued','supervisor_started','supervisor_exited'):
                raise ValueError('Invalid queue entry')
            config = load_config(row['config'])
            if (config.runs_root != Path(row['runs_root']) or config.runs_root in roots or
                    len(config.case_ids or ()) != row['expected'] or len(set(config.case_ids or ())) != row['expected']):
                raise ValueError('Matrix has unexpected cases or conflicts with another run root')
            names.add(name)
            roots.add(config.runs_root)
            command = [str(runtime/'python'), '-m', 'pixelworld.cli', 'matrix', 'run',
                       '--config', str(config.source), '--interval', '20']
            commands[name] = command
            if row['state'] == 'supervisor_started':
                identity = process_identity(row['pid'])
                if not identity or identity[1] != command or row.get('pid_start_marker', identity[0]) != identity[0]:
                    raise ValueError('Cannot prove the identity of an existing supervisor')
                row.update(pid_start_marker=identity[0], adopted=True)
        queues[state] = rows
    return queues, commands


def drive(groups, approved, runtime, repo, lanes, receipt):
    children = {}
    with ExitStack() as stack:
        for spec in sorted(groups):
            handle = stack.enter_context(spec.with_suffix('.lock').open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        queues, commands = read_queue(groups,approved,runtime)
        for state, rows in queues.items():
            atomic_json(state,rows)
        atomic_json(receipt,{'pid':os.getpid(),'lanes':lanes,'registered':len(commands),
                             'groups':[p.name for p in groups],'started_utc':utc_now()})
        while True:
            active = 0
            for state, rows in queues.items():
                dirty = False
                for row in rows:
                    if row['state'] != 'supervisor_started':
                        continue
                    if process_identity(row['pid']) == (row['pid_start_marker'],commands[row['name']]):
                        active += 1
                    else:
                        child = children.get(row['name'])
                        row.update(state='supervisor_exited',returncode=child.poll() if child else None,
                                   finished_at=time.time())
                        dirty = True
                if dirty:
                    atomic_json(state,rows)
            waiting = sorted(((state,row) for state, rows in queues.items() for row in rows
                              if row['state']=='queued'), key=lambda entry: entry[1]['name'])
            for state,row in waiting[:max(0,lanes-active)]:
                name = row['name']
                log = state.parent/(name+'.log')
                with log.open('ab') as output:
                    child = subprocess.Popen(commands[name],cwd=repo,env={**os.environ,'PIXELWORLD_ARCHIVE_BENCHMARK':name},
                                             stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
                identity = process_identity(child.pid)
                if identity and identity[1] == commands[name]:
                    children[name] = child
                    row.update(state='supervisor_started',pid=child.pid,pid_start_marker=identity[0],
                               adopted=False,log=str(log),started_at=time.time())
                else:
                    row.update(state='supervisor_exited',returncode=child.poll(),finished_at=time.time())
                atomic_json(state,queues[state])
            if not waiting and not active:
                break
            time.sleep(20)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--groups',type=Path,nargs='+',required=True)
    p.add_argument('--registered',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--lanes',type=int,required=True)
    p.add_argument('--validate-only',action='store_true')
    a = p.parse_args()
    if not 1 <= a.lanes <= 8:
        p.error('Use one to eight cohort lanes')
    runtime = Path(os.environ['PIXELWORLD_QUEUE_RUNTIME']).resolve(strict=True)
    repo = Path(os.environ['PIXELWORLD_QUEUE_REPO']).resolve(strict=True)
    paths = [p.resolve(strict=True) for p in a.groups]
    if len(paths) != len(set(paths)):
        p.error('Pass each registered group once')
    if a.validate_only:
        q, c = read_queue(paths,a.registered,runtime)
        print(json.dumps({'registered':len(c),'adoptable':sum(x['state']=='supervisor_started'for rows in q.values()for x in rows)}))
    else:
        os.umask(0o077)
        drive(paths,a.registered,runtime,repo,a.lanes,a.receipt)


if __name__ == '__main__':
    main()
