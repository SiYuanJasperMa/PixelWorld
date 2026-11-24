"""Continue local H3 smoke, full generation shards, and pinned PCVE scoring."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from pixelworld.util import atomic_json,sha256,utc_now
from tools.generate_minimax_pcve import export_window


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def evaluation_scope(base, full129=False):
    """Keep the ten-case calibration intact when expanding to official full129."""
    subset = None if full129 else base/'subset.json'
    total = 129 if full129 else read(subset).get('case_count',129)
    folder = 'evaluation_subset' if subset is not None and subset.exists() else 'evaluation'
    return subset, total, folder


def remote_jobs(worker,bundle):
    script='''import json,sys
from pathlib import Path
rows=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  if (p/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':continue
  a=[x.decode()for x in(p/'cmdline').read_bytes().split(b'\\0')if x]
  if sys.argv[1]in a and 'tools.generate_minimax_pcve'in a:rows.append(int(a[a.index('--shard-index')+1]))
 except (OSError,ValueError,UnicodeError):pass
print(json.dumps(rows))
'''
    try:
        r=subprocess.run(['ssh','-p','22222','-o','BatchMode=yes','-o','ConnectTimeout=10',worker,
            'python3 -c '+shlex.quote(script)+' '+shlex.quote(str(bundle))],capture_output=True,text=True,timeout=25,check=True)
        return {'worker':worker,'verified':True,'shards':json.loads(r.stdout)}
    except (subprocess.SubprocessError,ValueError):return {'worker':worker,'verified':False,'shards':[]}


def launch(worker,command,runtime,env,log):
    log.parent.mkdir(parents=True,exist_ok=True)
    script='import os,subprocess,json;os.chdir('+repr(str(runtime))+');os.environ.update('+repr(env)+');l=open('+repr(str(log))+',"ab");p=subprocess.Popen('+repr(command)+',stdin=subprocess.DEVNULL,stdout=l,stderr=subprocess.STDOUT,start_new_session=True);print(json.dumps({"pid":p.pid}))'
    r=subprocess.run(['ssh','-p','22222','-o','BatchMode=yes','-o','ConnectTimeout=10',worker,
        'python3 -c '+shlex.quote(script)],capture_output=True,text=True,timeout=25,check=True)
    return json.loads(r.stdout)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--worker',action='append',required=True);p.add_argument('--interval',type=float,default=60)
    p.add_argument('--parallel-smoke',action='store_true')
    p.add_argument('--full129',action='store_true',help='Score all official129 cases while preserving the calibration subset')
    p.add_argument('--generation-subset',type=Path)
    p.add_argument('--gpu-counts',default='')
    args=p.parse_args();code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve();base=bundle/'baselines/minimax_h3_local'
    runtime=code/'vendor/minimax-h3-runtime-pinned';h3py=code.parent/'fastvideo_h3_official_clean_20260926/.venv/bin/python'
    evalpy=code/'vendor/robojudge-eval-night/detached-runtime/bin/python';ffmpeg=bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg'
    weights=code.parent/'models/MiniMax-H3-official-fastvideo-clean';state=read(base/'dispatch.json')
    subset,total,evalfolder=evaluation_scope(base,args.full129)
    generation_subset=args.generation_subset or subset
    gpu_counts=[int(x)for x in args.gpu_counts.split(',')]if args.gpu_counts else[4]*len(args.worker)
    if len(gpu_counts)!=len(args.worker):raise ValueError('One GPU count per worker required')
    watch_lock=(base/'watch.lock').open('a');fcntl.flock(watch_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    env={'PYTHONPATH':str(code)+':'+str(runtime),'HF_HOME':str(bundle/'state/h3_hf_cache'),
         'XDG_CACHE_HOME':str(bundle/'state/h3_cache'),'TORCH_HOME':str(bundle/'state/h3_cache/torch'),
         'CUDA_CACHE_PATH':str(bundle/'state/h3_cache/cuda'),'TRITON_CACHE_DIR':str(bundle/'state/h3_cache/triton'),
         'TORCHINDUCTOR_CACHE_DIR':str(bundle/'state/h3_cache/inductor'),
         'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'8','NCCL_DEBUG':'WARN'}
    while True:
        records=[read(p)for p in (base/'cases').glob('pcve_*/generation.json')]
        complete=[r for r in records if r.get('status')=='completed'];smoke=read(base/'smoke/gpu_state.json')
        progress={'phase':'smoke_running','completed':len(complete),'total':total,'updated_utc':utc_now(),
                  'model':'MiniMaxAI/MiniMax-H3','model_revision':'42ed227ee7df40d41602854ae760620d6eb651fe',
                  'route':'local full checkpoint Ref2VA','native_canvas':[1344,768],'fps':24,'evaluation_frames':96,
                  'raw_requested_frames':124,'sampling_grid_points':50,'failed_cases':sum(r.get('status')=='failed'for r in records)}
        if smoke.get('phase')=='failed':
            progress.update(phase='smoke_failed',error=smoke);atomic_json(base/'progress.json',progress);return
        if (smoke.get('phase')=='finished'and smoke.get('returncode')==0)or args.parallel_smoke:
            c=base/'cases/pcve_0000';c.mkdir(parents=True,exist_ok=True)
            if smoke.get('phase')=='finished'and smoke.get('returncode')==0 and not read(c/'generation.json').get('status')=='completed':
                row=json.loads((code/'data/pcve-rigid/benchmark_manifest.json').read_text())['edits'][0]
                raw=base/'smoke/raw/minimax_h3_ref2va.mp4';copy=c/'raw.mp4'
                if not copy.exists():shutil.copy2(raw,copy)
                target=base/'predictions/minimax_h3_local/videos'/row['scene']/(row['case_id']+'.mp4')
                raw_media,media=export_window(copy,target,ffmpeg)
                atomic_json(c/'generation.json',{'case_id':'pcve_0000','global_id':row['global_id'],'status':'completed',
                    'prompt':row['prompts']['quantitative']['en'],'source_video_sha256':sha256(code/'data/pcve-rigid'/row['source_video']),
                    'raw_sha256':sha256(copy),'export_sha256':sha256(target),'raw_media':raw_media,'evaluated_media':media,
                    'seed':42,'model_revision':progress['model_revision'],'time_rescaling':False,'completed_utc':utc_now(),'from_smoke':True})
            progress['phase']='generation_running'
            if read(c/'generation.json').get('status')=='completed'and not state.get('smoke_evaluation'):
                tmp=code.parent/'.pwtmp/h3_eval_s';tmp.mkdir(parents=True,exist_ok=True)
                command=[str(evalpy),'-u','-m','tools.run_gpu_evaluation','--state-root',str(bundle/'state/gpu_claims'),
                    '--status',str(base/'smoke_evaluation/gpu_state.json'),'--min-free-gib','32','--',str(evalpy),'-u',
                    '-m','tools.evaluate_minimax_pcve','--bundle',str(bundle),'--limit','1']
                state['smoke_evaluation']=launch(args.worker[0],command,code,{**env,'TMPDIR':str(tmp)},base/'logs/smoke_evaluation.log');atomic_json(base/'dispatch.json',state)
            with ThreadPoolExecutor(max_workers=len(args.worker))as pool:health=list(pool.map(lambda w:remote_jobs(w,bundle),args.worker))
            active={i for h in health for i in h['shards']};progress['running_shards']=len(active);progress['worker_health_verified']=all(h['verified']for h in health)
            gpu_states=[read(base/f'gpu/shard_{i}.json')for i in range(len(args.worker))]
            progress['allocated_gpus']=(len(smoke.get('gpus',[]))if smoke.get('phase')=='running' else 0)+sum(len(g.get('gpus',[]))for g in gpu_states if g.get('phase')=='running')
            progress['waiting_gpu_shards']=sum(g.get('phase')=='waiting_for_free_gpu_group'for g in gpu_states)
            if all(h['verified']for h in health):
                for i,worker in enumerate(args.worker):
                    previous=state.get(str(i),{});s=read(base/f'shard_{i}.json')
                    if i in active or s.get('phase')=='finished'or previous.get('retry_after',0)>time.time():continue
                    if previous.get('launches',0)>=3:continue
                    tmp=code.parent/f'.pwtmp/h3_{i}';tmp.mkdir(parents=True,exist_ok=True)
                    command=[str(evalpy),'-u','-m','tools.run_gpu_group','--state-root',str(bundle/'state/gpu_claims'),
                        '--status',str(base/f'gpu/shard_{i}.json'),'--count',str(gpu_counts[i]),'--min-free-gib','70','--',
                        str(h3py),'-u','-m','tools.generate_minimax_pcve','--bundle',str(bundle),'--runtime',str(runtime),
                        '--model-path',str(weights),'--ffmpeg',str(ffmpeg),'--shard-index',str(i),
                        '--num-shards',str(len(args.worker)),'--num-gpus',str(gpu_counts[i])]
                    if generation_subset is not None and generation_subset.exists():command+=['--subset',str(generation_subset)]
                    result=launch(worker,command,runtime,{**env,'TMPDIR':str(tmp)},base/f'logs/shard_{i}.log')
                    state[str(i)]={'worker':worker,**result,'launches':previous.get('launches',0)+1,'retry_after':time.time()+600}
                    atomic_json(base/'dispatch.json',state)
            if len(complete)==total:
                progress['phase']='evaluating';summary=read(base/evalfolder/'summary.json')
                if summary.get('case_count')==total and summary.get('scores'):
                    progress.update(phase='finished',scores=summary['scores']);atomic_json(base/'progress.json',progress);return
                gpu=read(base/evalfolder/'gpu_state.json')
                if not state.get('evaluation')or gpu.get('phase')=='failed':
                    tmp=code.parent/'.pwtmp/h3_eval';tmp.mkdir(parents=True,exist_ok=True)
                    command=[str(evalpy),'-u','-m','tools.run_gpu_evaluation','--state-root',str(bundle/'state/gpu_claims'),
                        '--status',str(base/evalfolder/'gpu_state.json'),'--min-free-gib','32','--',str(evalpy),'-u',
                        '-m','tools.evaluate_minimax_pcve','--bundle',str(bundle)]
                    if subset is not None and subset.exists():command+=['--subset',str(subset)]
                    result=launch(args.worker[0],command,code,{**env,'TMPDIR':str(tmp)},base/'logs/evaluation.log');state['evaluation']=result;atomic_json(base/'dispatch.json',state)
        atomic_json(base/'progress.json',progress)
        time.sleep(args.interval)


if __name__=='__main__':main()
