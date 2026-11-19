"""Run a restartable detached matrix against a fixed code checkout."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

def alive(pid, required_arguments=()):
    try:
        os.kill(int(pid),0)
        proc=Path('/proc')/str(pid)
        if proc.exists():
            if (proc/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':return False
            if required_arguments:
                arguments=(proc/'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
                if not all(value in arguments for value in required_arguments):return False
        return True
    except FileNotFoundError:return False
    except (ValueError,TypeError,ProcessLookupError,PermissionError):return False

def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}

def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)

def dispatcher_disk_stall(pid, status_path, required_arguments, stale_seconds=900,
                          proc_root=Path('/proc'), now=None):
    """Recognize a blocked dispatcher, never a queued model request."""
    if stale_seconds <= 0:return False
    try:
        age=(time.time() if now is None else now)-status_path.stat().st_mtime
        if age < stale_seconds:return False
        proc=proc_root/str(int(pid))
        args=(proc/'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
        if not all(value in args for value in required_arguments):return False
        if (proc/'stat').read_text().rsplit(') ',1)[1].split()[0]!='D':return False
        children=(proc/'task'/str(int(pid))/'children').read_text().split()
        for child in children:
            try:state=(proc_root/child/'stat').read_text().rsplit(') ',1)[1].split()[0]
            except FileNotFoundError:continue
            if state!='Z':return False
        return True
    except (OSError,ValueError,TypeError,IndexError):return False

def recover_disk_dispatcher(pid, status_path, required_arguments, stale_seconds):
    """Pin the process identity before checking and signaling it on Linux."""
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):return False
    try:fd=os.pidfd_open(int(pid))
    except (OSError,ValueError,TypeError):return False
    try:
        if not dispatcher_disk_stall(pid,status_path,required_arguments,stale_seconds):return False
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        return True
    except OSError:return False
    finally:os.close(fd)

def completed_scope(config,status):
    identifiers=config.get('case_ids')
    if not identifiers:
        identifiers=[p.name for p in Path(config['inputs_root']).iterdir() if (p/'prompt.txt').is_file()]
    expected=len(identifiers)*sum(len(m.get('tracks',['text','vision-agent'])) for m in config['models'])
    counts=status.get('totals',{})
    return expected>0 and status.get('total_runs')==expected and counts.get('complete',0)+counts.get('exhausted',0)==expected

def authoritative_scope_complete(config_path, code_root):
    """Recheck retry windows before accepting a stale terminal status hint."""
    sys.path.insert(0, str(code_root.resolve()))
    from pixelworld.config import load_config
    from pixelworld.supervisor import case_ids, configurations, inspect
    config=load_config(config_path)
    return all(inspect(config,model,track,case)['state'] in ('complete','exhausted')
               for model,track in configurations(config) for case in case_ids(config))

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--code-root',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--resume',action='store_true')
    p.add_argument('--dispatcher-stale-seconds',type=float,default=900,
                   help='Recover disk-blocked dispatchers with no active children; zero disables')
    a=p.parse_args()
    if a.dispatcher_stale_seconds<0:p.error('--dispatcher-stale-seconds must be non-negative')
    cfg=read(a.config);root=Path(cfg['runs_root']);root.mkdir(parents=True,exist_ok=True);a.state.parent.mkdir(parents=True,exist_ok=True)
    stop=root/'STOP';managed_stop=a.state.with_name(a.state.stem+'.STOP')
    if a.resume:stop.unlink(missing_ok=True);managed_stop.unlink(missing_ok=True)
    child=None;restarts=0;recovery_pid=None;env=os.environ.copy();env['PYTHONPATH']=str(a.code_root.resolve())
    while not stop.exists() and not managed_stop.exists():
        latest=read(a.config)
        if latest.get('runs_root')==cfg.get('runs_root') and latest.get('models'):
            cfg=latest
        status=read(root/'status.json');counts=status.get('totals',{});total=status.get('total_runs',0)
        if completed_scope(cfg,status) and authoritative_scope_complete(a.config,a.code_root):
            write(a.state,{'phase':'processed_all_requested_tasks','counts':counts,'total':total,'failures_preserved':True,'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()});return
        owner=status.get('supervisor_pid')
        identity=('pixelworld.cli','matrix','run',str(a.config.resolve()))
        recovering=False
        if owner!=recovery_pid and recover_disk_dispatcher(owner,root/'status.json',identity,a.dispatcher_stale_seconds):
            recovery_pid=owner;recovering=True
            print(json.dumps({'event':'dispatcher_disk_stall_recovery','supervisor_pid':owner,
                              'active_case_children':0,'generation_requests_restarted':False}),flush=True)
        active=child is not None and child.poll() is None
        if not active and not alive(owner,identity):
            with a.state.with_suffix('.log').open('ab') as log:
                child=subprocess.Popen([sys.executable,'-m','pixelworld.cli','matrix','run','--config',str(a.config.resolve())],cwd=a.code_root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            owner=child.pid;restarts+=1
        write(a.state,{'phase':'recovering_dispatcher' if recovering else 'running','manager_pid':os.getpid(),'supervisor_pid':owner,'starts':restarts,'counts':counts,'total':total,'code_root':str(a.code_root.resolve()),'config':str(a.config.resolve()),'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
        time.sleep(30)
    write(a.state,{'phase':'explicitly_stopped','updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()})

if __name__=='__main__':main()
