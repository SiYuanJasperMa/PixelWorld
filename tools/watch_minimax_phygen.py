"""Run local H3 PhyGen generation followed by the shared three-stage GPT5.5 judge."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from pixelworld.util import atomic_json,utc_now
from tools.watch_minimax_pcve import launch,read


def remote_launcher_alive(worker,pid,status):
    script='''from pathlib import Path
import json,sys
p=Path('/proc')/sys.argv[1]
try:
 a=[x.decode()for x in(p/'cmdline').read_bytes().split(b'\\0')if x]
 alive=(p/'stat').read_text().rsplit(') ',1)[1].split()[0]!='Z'
 print(json.dumps(alive and 'tools.run_gpu_group'in a and sys.argv[2]in a))
except OSError:print('false')
'''
    result=subprocess.run(['ssh','-p','22222','-o','BatchMode=yes','-o','ConnectTimeout=10',worker,
        'python -c '+shlex.quote(script)+' '+str(pid)+' '+shlex.quote(str(status))],
        capture_output=True,text=True,timeout=25,check=True)
    return json.loads(result.stdout)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--worker',required=True)
    parser.add_argument('--gpu-count',type=int,default=2)
    parser.add_argument('--interval',type=float,default=60)
    args=parser.parse_args();code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve()
    base=bundle/'baselines/minimax_h3_phygen';base.mkdir(parents=True,exist_ok=True)
    guard=(base/'watch.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    dispatch=read(base/'dispatch.json');runtime=code/'vendor/minimax-h3-runtime-pinned'
    h3py=code.parent/'fastvideo_h3_official_clean_20260926/.venv/bin/python'
    launchpy=code/'vendor/robojudge-eval-night/detached-runtime/bin/python'
    tmp=code.parent/'.pwtmp/h3p';tmp.mkdir(parents=True,exist_ok=True)
    cache=bundle/'state/h3_phygen_cache';cache.mkdir(parents=True,exist_ok=True)
    env={'PYTHONPATH':str(code)+':'+str(runtime),'TMPDIR':str(tmp),
        'HF_HOME':str(cache/'hf'),'XDG_CACHE_HOME':str(cache),'TORCH_HOME':str(cache/'torch'),
        'CUDA_CACHE_PATH':str(cache/'cuda'),'TRITON_CACHE_DIR':str(cache/'triton'),
        'TORCHINDUCTOR_CACHE_DIR':str(cache/'inductor'),'HF_HUB_OFFLINE':'1',
        'TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'8','NCCL_DEBUG':'WARN'}
    gpu_status=base/'gpu_state.json';evaluation=bundle/'evaluation/minimax_h3_phygen_gpt55_three_stage_mcq_v1'
    while True:
        records=[read(f)for f in(base/'cases').glob('*/generation.json')]
        completed=sum(r.get('status')=='completed'for r in records)
        progress=read(base/'progress.json')or{'phase':'waiting_for_free_gpu_group','total':160}
        gpu=read(gpu_status)
        donor=read(base/'backfill/gpu_state.json')
        allocated=(len(gpu.get('gpus',[]))if gpu.get('phase')=='running'else 0)+(len(donor.get('gpus',[]))if donor.get('phase')=='running'else 0)
        progress.update(completed=completed,total=160,allocated_gpus=allocated,
            failed_cases=sum(r.get('status')=='failed'for r in records),updated_utc=utc_now())
        if completed<160:
            live=False
            if dispatch.get('generation'):
                try:live=remote_launcher_alive(args.worker,dispatch['generation']['pid'],gpu_status)
                except(subprocess.SubprocessError,ValueError):
                    progress['worker_health_verified']=False
                    atomic_json(base/'progress.json',progress);time.sleep(args.interval);continue
            if live:
                if gpu.get('phase')=='waiting_for_free_gpu_group':progress['phase']='waiting_for_free_gpu_group'
            else:
                count=dispatch.get('generation_launches',0)
                if count>=3:
                    progress.update(phase='generation_failed',error='Three owned generator launches failed; inspect generation.log')
                    atomic_json(base/'progress.json',progress);return
                command=[str(launchpy),'-u','-m','tools.run_gpu_group','--state-root',str(bundle/'state/gpu_claims'),
                    '--status',str(gpu_status),'--count',str(args.gpu_count),'--min-free-gib','70','--',
                    str(h3py),'-u','-m','tools.generate_minimax_phygen','--bundle',str(bundle),
                    '--runtime',str(runtime),'--model-path',str(code.parent/'models/MiniMax-H3-official-fastvideo-clean'),
                    '--ffmpeg',str(bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg'),
                    '--num-gpus',str(args.gpu_count)]
                dispatch.update(generation=launch(args.worker,command,code,env,base/'logs/generation.log'),
                    generation_launches=count+1,worker=args.worker)
                atomic_json(base/'dispatch.json',dispatch)
                progress['phase']='waiting_for_free_gpu_group'
        else:
            summary=read(evaluation/'summary.json');entry=summary.get('models',{}).get('minimax-h3-local',{})
            if summary.get('full160')is True and entry.get('cases')==160 and not summary.get('failures'):
                progress.update(phase='finished',evaluation_completed=160,evaluation_total=160,score=entry['mean'])
                atomic_json(base/'progress.json',progress);return
            progress.update(phase='evaluating',evaluation_total=160,
                evaluation_completed=len([f for f in(evaluation/'minimax-h3-local').glob('*.json')if f.stem.isdigit()]))
            pid=dispatch.get('evaluation_pid');proc=Path('/proc')/str(pid)
            alive=False
            if pid:
                try:
                    argv=[x.decode()for x in(proc/'cmdline').read_bytes().split(b'\0')if x]
                    alive='tools.evaluate_phygen_three_stage_gpt55'in argv and str(evaluation)in argv
                except OSError:pass
            if not alive:
                if dispatch.get('evaluation_launches',0)>=3:
                    progress.update(phase='evaluation_failed',error='Evaluator retries exhausted; inspect judge.log')
                    atomic_json(base/'progress.json',progress);return
                command=[sys.executable,'-u','-m','tools.evaluate_phygen_three_stage_gpt55','--bundle',str(bundle),
                    '--output',str(evaluation),'--models','minimax-h3-local','--workers','32']
                evaluation.mkdir(parents=True,exist_ok=True);localenv=os.environ.copy();localenv.update(env)
                with(base/'logs/judge.log').open('ab')as log:
                    process=subprocess.Popen(command,cwd=code,env=localenv,stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                dispatch.update(evaluation_pid=process.pid,evaluation_launches=dispatch.get('evaluation_launches',0)+1)
                atomic_json(base/'dispatch.json',dispatch)
        atomic_json(base/'progress.json',progress)
        time.sleep(args.interval)


if __name__=='__main__':main()
