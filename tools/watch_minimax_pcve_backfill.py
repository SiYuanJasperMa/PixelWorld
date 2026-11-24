"""Reuse the finished A800 worker for a disjoint, bounded PCVE tail batch."""
import argparse
import fcntl
import json
from pathlib import Path
import statistics
import time

from pixelworld.util import atomic_json,utc_now
from tools.watch_minimax_pcve import launch,read
from tools.watch_minimax_phygen import remote_launcher_alive


def select_tail_batch(records,slow_indices,limit=12,slow_seconds=697,fast_seconds=385,startup_seconds=600):
    active={i for i,r in records.items()if r.get('status')=='generating'}
    pending=sorted(i for i in slow_indices if records.get(i,{}).get('status')!='completed'and i not in active)
    for count in range(min(limit,len(pending)),0,-1):
        selected=pending[-count:]
        ahead=len([i for i in pending if i<min(selected)])
        if ahead*slow_seconds>1.5*(startup_seconds+count*fast_seconds):
            return list(reversed(selected))
    return []


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--worker',required=True)
    parser.add_argument('--interval',type=float,default=30)
    args=parser.parse_args();code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve()
    base=bundle/'baselines/minimax_h3_local';root=base/'backfill';root.mkdir(parents=True,exist_ok=True)
    guard=(root/'watch.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    status=base/'gpu/shard_1.json';dispatch=read(root/'dispatch.json')
    runtime=code/'vendor/minimax-h3-runtime-pinned';h3py=code.parent/'fastvideo_h3_official_clean_20260926/.venv/bin/python'
    launchpy=code/'vendor/robojudge-eval-night/detached-runtime/bin/python'
    tmp=code.parent/'.pwtmp/h3a';tmp.mkdir(parents=True,exist_ok=True)
    cache=bundle/'state/h3_cache'
    env={'PYTHONPATH':str(code)+':'+str(runtime),'TMPDIR':str(tmp),
        'HF_HOME':str(bundle/'state/h3_hf_cache'),'XDG_CACHE_HOME':str(cache),
        'TORCH_HOME':str(cache/'torch'),'CUDA_CACHE_PATH':str(cache/'cuda'),
        'TRITON_CACHE_DIR':str(cache/'triton'),'TORCHINDUCTOR_CACHE_DIR':str(cache/'inductor'),
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false',
        'OMP_NUM_THREADS':'8','NCCL_DEBUG':'WARN'}
    while True:
        records={int(f.parent.name.split('_')[1]):read(f)for f in(base/'cases').glob('*/generation.json')}
        if not dispatch.get('subset'):
            fast=read(base/'shard_1.json');gpu=read(status)
            if fast.get('phase')!='finished'or gpu.get('phase')!='finished':
                atomic_json(root/'progress.json',{'phase':'waiting_for_original_shard','updated_utc':utc_now()})
                time.sleep(args.interval);continue
            all_indices=json.loads((base/'full129_remaining.json').read_text())['indices']
            slow=all_indices[::2];fast_indices=set(all_indices[1::2])
            completed=sorted([(i,r)for i,r in records.items()if r.get('status')=='completed'and r.get('elapsed_seconds')],key=lambda item:item[1]['completed_utc'])
            slow_durations=[r['elapsed_seconds']for i,r in completed if i in slow]
            fast_durations=[r['elapsed_seconds']for i,r in completed if i in fast_indices]
            selected=select_tail_batch(records,slow,slow_seconds=statistics.median(slow_durations[-8:])if slow_durations else 697,
                fast_seconds=statistics.median(fast_durations[-8:])if fast_durations else 385)
            if not selected:
                atomic_json(root/'complete.json',{'complete':True,'reason':'No sufficiently separated tail batch','updated_utc':utc_now()});return
            subset=root/'subset.json';atomic_json(subset,{'indices':selected,'case_count':len(selected),
                'selection_rule':'Pending slow-shard tail; exclude active/completed; predicted1.5x separation budget',
                'selected_utc':utc_now()})
            dispatch['subset']=selected;atomic_json(root/'dispatch.json',dispatch)
        selected=dispatch['subset'];done=sum(records.get(i,{}).get('status')=='completed'for i in selected)
        if done==len(selected):
            atomic_json(root/'complete.json',{'complete':True,'cases':done,'updated_utc':utc_now()});return
        live=False
        if dispatch.get('pid'):
            try:live=remote_launcher_alive(args.worker,dispatch['pid'],status)
            except Exception:
                time.sleep(args.interval);continue
        if not live:
            if dispatch.get('launches',0)>=3:
                atomic_json(root/'progress.json',{'phase':'failed','completed':done,'total':len(selected),'updated_utc':utc_now()});return
            command=[str(launchpy),'-u','-m','tools.run_gpu_group','--state-root',str(bundle/'state/gpu_claims'),
                '--status',str(status),'--count','8','--min-free-gib','70','--',str(h3py),'-u','-m',
                'tools.generate_minimax_pcve','--bundle',str(bundle),'--runtime',str(runtime),
                '--model-path',str(code.parent/'models/MiniMax-H3-official-fastvideo-clean'),
                '--ffmpeg',str(bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg'),
                '--shard-index','1','--num-shards','2','--num-gpus','8','--subset',str(root/'subset.json'),
                '--all-subset','--state-path',str(root/'generation.json')]
            result=launch(args.worker,command,code,env,root/'generation.log')
            dispatch.update(**result,launches=dispatch.get('launches',0)+1);atomic_json(root/'dispatch.json',dispatch)
        atomic_json(root/'progress.json',{'phase':'backfill_running','completed':done,'total':len(selected),
            'updated_utc':utc_now(),'worker':args.worker})
        time.sleep(args.interval)


if __name__=='__main__':main()
