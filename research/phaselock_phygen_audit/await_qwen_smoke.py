"""Start the initial-state smoke when the isolated model runtime is ready."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import fcntl

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'runs/phygen_i2v_qwen21_20261111'

def main():
    lock=(RUN/'logs/smoke_controller.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    status=RUN/'logs/smoke_controller.json'
    while True:
        try:ready=json.loads((RUN/'logs/setup_status.json').read_text())
        except (OSError,ValueError):ready={}
        status.write_text(json.dumps({'phase':'waiting_for_runtime','pid':os.getpid(),'updated_at':time.time(),'setup_phase':ready.get('phase')},indent=2))
        if ready.get('phase')=='ready':break
        time.sleep(30)
    files=[RUN/'candidates'/f'phygen_{i+1:03d}'/f'candidate_{j:02d}.png' for i in [0,60,90,120] for j in range(3)]
    if not all(p.is_file() for p in files):
        status.write_text(json.dumps({'phase':'generating_smoke','pid':os.getpid(),'updated_at':time.time()},indent=2))
        code=subprocess.call([sys.executable,'-u',str(ROOT/'research/phaselock_phygen_audit/run_qwen_gpu_lease.py'),'--gpu','0','--mode','smoke'],cwd=ROOT)
        if code:
            status.write_text(json.dumps({'phase':'smoke_failed','returncode':code,'updated_at':time.time()},indent=2))
            return
    status.write_text(json.dumps({'phase':'awaiting_actual_visual_review','candidates':list(map(str,files)),'updated_at':time.time(),'full_generation_started':False},indent=2))

if __name__=='__main__':main()
