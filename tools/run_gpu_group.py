"""Hold coordinated claims for a group of idle GPUs while running a model."""
import argparse
import fcntl
import itertools
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from pixelworld.util import atomic_json
from tools.run_gpu_evaluation import free_gpus


def claim_group(root, count, minimum_mib):
    available={i:m for i,m in free_gpus() if m>=minimum_mib}
    for indices in itertools.combinations(sorted(available),count):
        handles=[]
        try:
            for index in indices:
                handle=(root/f'gpu_{index}.lock').open('a');handles.append(handle)
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fresh=dict(free_gpus())
            if all(fresh.get(i,0)>=minimum_mib for i in indices):
                return indices,handles
        except BlockingIOError:
            pass
        for handle in handles:handle.close()
    return None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-root',type=Path,required=True)
    p.add_argument('--status',type=Path,required=True)
    p.add_argument('--count',type=int,default=4)
    p.add_argument('--min-free-gib',type=float,default=70)
    p.add_argument('--interval',type=float,default=30)
    p.add_argument('command',nargs=argparse.REMAINDER)
    args=p.parse_args();command=args.command[1:]if args.command[:1]==['--']else args.command
    if not command or args.count<1 or args.min_free_gib<=0 or args.interval<=0:p.error('Positive group size, memory and interval required')
    host=socket.gethostname();root=args.state_root.resolve()/host;root.mkdir(parents=True,exist_ok=True)
    def record(**values):
        atomic_json(args.status,{'host':host,'launcher_pid':os.getpid(),'updated_at':time.time(),**values})
    while True:
        selected=claim_group(root,args.count,int(args.min_free_gib*1024))
        if selected:break
        record(phase='waiting_for_free_gpu_group',required_gpus=args.count,required_free_gib=args.min_free_gib)
        time.sleep(args.interval)
    indices,handles=selected;env=os.environ.copy();env['CUDA_VISIBLE_DEVICES']=','.join(map(str,indices))
    process=None
    def terminate(signum,_):
        if process is not None and process.poll() is None:os.killpg(process.pid,signum)
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    try:
        process=subprocess.Popen(command,env=env,start_new_session=True)
        record(phase='running',gpus=list(indices),child_pid=process.pid)
        code=process.wait();record(phase='finished'if code==0 else'failed',gpus=list(indices),child_pid=process.pid,returncode=code)
        return code
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        for handle in handles:handle.close()


if __name__=='__main__':raise SystemExit(main())
