"""Keep an explicitly listed benchmark service chain alive without changing tasks."""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from tools.manage_matrix import recover_disk_dispatcher


def service_pid(service, start=None):
    """A just-started child is authoritative even if its PID receipt did not fit."""
    candidates = [start['pid']] if start else []
    try:
        candidates.append(int(Path(service['pid_file']).read_text().strip()))
    except (OSError, ValueError):
        pass
    for pid in candidates:
        proc = Path('/proc')/str(pid)
        try:
            if proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                continue
            args = [x.decode(errors='replace') for x in proc.joinpath('cmdline').read_bytes().split(b'\0') if x]
            if all(token in args for token in service['identity']):
                return pid
        except (OSError, IndexError):
            continue
    return None


def record_start(service, pid, starts):
    starts[service['name']] = {'pid': pid, 'time': time.monotonic()}
    try:
        pid_file = Path(service['pid_file'])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = pid_file.with_name(pid_file.name + '.watchdog.tmp')
        temporary.write_text(str(pid)+'\n')
        temporary.replace(pid_file)
    except OSError as exc:
        return f'{type(exc).__name__}: {exc}'
    return None


def complete_service(service):
    path=service.get('completion_path')
    checks=service.get('completion_checks')
    if not path or not checks:return False
    try:
        result=json.loads(Path(path).read_text())
        return all(key in result and result[key]==value for key,value in checks.items())
    except(OSError,ValueError):return False


def recover_stale_output(service,recoveries,starts,now=None,pid=None):
    """Opt-in recovery for a disk-blocked service with stale output."""
    marker=service.get('health_path')
    if not marker:return False
    now=time.monotonic() if now is None else now
    name=service['name'];limit=service.get('max_output_age_seconds',900)
    if pid is None:
        pid=int(Path(service['pid_file']).read_text().strip())
    start=starts.get(name)
    if start and start['pid']==pid and now-start['time']<60:return False
    try:
        if time.time()-Path(marker).stat().st_mtime<limit:
            recoveries.pop(name,None);return False
    except OSError:return False
    previous=recoveries.get(name)
    same=previous and previous['pid']==pid
    if same and (previous.get('escalated') or now-previous['time']<60):return True
    import signal
    sig=signal.SIGKILL if same else signal.SIGTERM
    if not recover_disk_dispatcher(pid,Path(marker),tuple(service['identity']),limit,signal_number=sig):return False
    recoveries[name]={'pid':pid,'time':previous['time'] if same else now,'escalated':bool(same)}
    return True


def run(spec_path):
    spec = json.loads(spec_path.read_text())
    root = Path(spec['state_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/'watchdog.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return
    (root/'watchdog.pid').write_text(str(os.getpid())+'\n')
    recoveries={};starts={}
    while not (root/'STOP').exists():
        reload_error=None
        try:
            updated=json.loads(spec_path.read_text())
            if Path(updated['state_root']).resolve()!=root.resolve():
                raise ValueError('Restart explicitly before changing the watchdog state directory')
            spec=updated
        except (OSError,ValueError,KeyError) as exc:
            reload_error=str(exc)
        states = {}
        for service in spec['services']:
            name = service['name']
            if complete_service(service):
                states[name]={'phase':'complete'}
                continue
            if service.get('stop_marker') and Path(service['stop_marker']).exists():
                states[name] = {'phase':'paused'}
                continue
            if any(states.get(dep, {}).get('phase') != 'active' for dep in service.get('depends_on', [])):
                states[name] = {'phase':'waiting_dependency'}
                continue
            pid = service_pid(service, starts.get(name))
            if pid is not None and recover_stale_output(service,recoveries,starts,pid=pid):
                states[name]={'phase':'recovering_io',**recoveries[name]}
                continue
            if pid is None:
                try:
                    environment = os.environ.copy()
                    environment.update(service.get('environment', {}))
                    for key, path in service.get('environment_files', {}).items():
                        environment[key] = Path(path).read_text().strip()
                    log = Path(service['log']); log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open('ab') as stream:
                        process = subprocess.Popen(service['command'], cwd=service['cwd'], env=environment,
                            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                    receipt_error = record_start(service, process.pid, starts)
                    recoveries.pop(name,None)
                    states[name] = {'phase':'starting','pid':process.pid,'started_at':time.time()}
                    if receipt_error:
                        states[name]['pid_receipt_error'] = receipt_error
                except (OSError,ValueError) as exc:
                    states[name] = {'phase':'stale','error':f'{type(exc).__name__}: {exc}'}
            else:
                states[name] = {'phase':'active','pid':pid}
        payload = {'pid':os.getpid(),'updated_at':time.time(),'services':states,
                   'scope':spec['scope'],'generation_rules_modified':False,'config_reload_error':reload_error}
        temporary = root/'health.tmp'
        try:
            temporary.write_text(json.dumps(payload, indent=2)+'\n')
            temporary.replace(root/'health.json')
        except OSError as exc:
            # Keep supervising already-launched children during quota pressure.
            if exc.errno not in (errno.EDQUOT, errno.ENOSPC):
                raise
        time.sleep(spec.get('interval',30))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    run(parser.parse_args().spec.resolve())
