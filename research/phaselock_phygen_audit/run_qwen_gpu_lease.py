"""Borrow only known idle GPU fillers, compute into the shared run, restore them."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT/'runs/phygen_i2v_qwen21_20261111'
FILLERS = {'/mnt/bn/embodied-lf3/masiyuan/gpu_watchdog_mystic/h20_keepalive_memfill.py',
           '/tmp/mystic_filler_h20_exact/filler.py'}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--mode', choices=['smoke','full'], required=True)
    parser.add_argument('--shard', type=int)
    parser.add_argument('--shards', type=int, default=8)
    parser.add_argument('--indices', default='0,60,90,120')
    args=parser.parse_args()
    saved=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv=[x.decode() for x in proc.joinpath('cmdline').read_bytes().split(b'\0') if x]
            if not any(path in argv for path in FILLERS):
                continue
            original=dict(x.decode().split('=',1) for x in proc.joinpath('environ').read_bytes().split(b'\0') if b'=' in x)
            if original.get('CUDA_VISIBLE_DEVICES') != str(args.gpu):
                continue
            keep={k:original[k] for k in ['CUDA_VISIBLE_DEVICES','LD_LIBRARY_PATH','PYTHONPATH','OMP_NUM_THREADS'] if k in original}
            saved.append({'pid':int(proc.name),'argv':argv,'environment':keep})
        except (OSError, ValueError):
            continue
    receipt=RUN/'logs'/f'gpu_{args.gpu}_{args.mode}_lease.json'
    receipt.write_text(json.dumps({'phase':'borrowing_idle_fillers','fillers':saved,'started_at':time.time()},indent=2))
    # Terminate just the inspected memory-fill jobs, never a training/eval job.
    for job in saved:
        try:os.kill(job['pid'],signal.SIGTERM)
        except ProcessLookupError:pass
    deadline=time.monotonic()+30
    while any(Path('/proc',str(job['pid'])).exists() for job in saved) and time.monotonic()<deadline:
        time.sleep(.25)
    environment=os.environ.copy()
    environment['CUDA_VISIBLE_DEVICES']=str(args.gpu)
    environment['HF_HOME']=str(RUN/'runtime/hf_home')
    environment['LD_LIBRARY_PATH']='/usr/local/cuda/extras/CUPTI/lib64:/usr/local/cuda/lib64'
    environment['TMPDIR']=str(RUN/'runtime/tmp')
    command=[str(RUN/'runtime/venv/bin/python'),'-u',str(ROOT/'research/phaselock_phygen_audit/generate_qwen_candidates.py'),'--run-dir',str(RUN)]
    if args.mode=='smoke':command+=['--indices',args.indices]
    else:command+=['--shard',str(args.shard if args.shard is not None else args.gpu),'--shards',str(args.shards)]
    code=None
    restored=[]
    try:
        code=subprocess.call(command,cwd=ROOT,env=environment)
    finally:
        for job in saved:
            env=os.environ.copy();env.update(job['environment'])
            with (RUN/'logs'/f'gpu_{args.gpu}_filler_restore.log').open('ab') as log:
                p=subprocess.Popen(job['argv'],env=env,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            restored.append(p.pid)
        receipt.write_text(json.dumps({'phase':'finished','returncode':code,'restored_fillers':restored,'original_fillers':saved,'completed_at':time.time()},indent=2))
    if code:raise SystemExit(code)

if __name__=='__main__':main()
