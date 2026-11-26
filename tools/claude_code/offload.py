#!/usr/bin/env python3
"""Move HF-verified, complete case run dirs to external storage and leave a symlink.
Only cases marked 'published' (read back from HF, hashes match) in the publication ledger.

Environment: PIXELWORLD_HOME (default $HOME/pixelworld_opus);
PIXELWORLD_OFFLOAD_DIR (destination, e.g. a mounted SSD folder). Offload is disabled if unset.
"""
import json, os, shutil, subprocess, sys, time, datetime as dt
from pathlib import Path
HOME = Path(os.environ.get('PIXELWORLD_HOME') or Path.home()/'pixelworld_opus')
LEDGER = HOME/'hf_publish/state/published_ledger.json'
LOG = HOME/'offload.log'
if not os.environ.get('PIXELWORLD_OFFLOAD_DIR'):
    sys.exit('PIXELWORLD_OFFLOAD_DIR is not set: offload disabled')
DEST = Path(os.environ['PIXELWORLD_OFFLOAD_DIR'])
def log(s):
    with open(LOG,'a') as f: f.write(f'{dt.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z {s}\n')
failed = set()
while True:
    try: led = json.loads(LEDGER.read_text())
    except Exception: led = {}
    for key, v in led.items():
        if v.get('status') != 'published' or not v.get('local_run'): continue
        run = Path(v['local_run'])
        if not str(run).startswith(str(HOME)+'/') or run.is_symlink() or not run.is_dir() or str(run) in failed: continue
        try:
            m = json.loads((run/'run_manifest.json').read_text())
        except Exception: continue
        if m.get('status') not in ('complete','failed') or any(run.rglob('*.pid')) and m.get('status')=='running': continue
        if any(p.is_symlink() for p in run.rglob('*')): log(f'SKIP has-symlink {run}'); continue
        target = DEST/run.relative_to(HOME)
        tmp = target.with_name(target.name+'.part')
        if tmp.exists(): shutil.rmtree(tmp)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(['cp','-r','--no-preserve=mode,ownership',str(run),str(tmp)],capture_output=True,text=True)
        if r.returncode: log(f'FAIL cp {run} (kept local): {r.stderr[:200]}'); failed.add(str(run)); shutil.rmtree(tmp, ignore_errors=True); continue
        d = subprocess.run(['diff','-rq',str(run),str(tmp)],capture_output=True,text=True)
        if d.returncode: log(f'FAIL diff {run}: {d.stdout[:200]}'); continue
        os.replace(tmp,target) if not target.exists() else None
        if not target.exists(): log(f'FAIL target {run}'); continue
        shutil.rmtree(run); os.symlink(target, run)
        log(f'OK {run} -> {target}')
    time.sleep(600)
