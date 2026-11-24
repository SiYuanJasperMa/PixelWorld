"""Generate bounded private T2V tail batches on the freed A800 worker."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import statistics
import time

from pixelworld.util import atomic_json,sha256,utc_now
from tools.watch_minimax_pcve import launch,read
from tools.watch_minimax_pcve_backfill import select_tail_batch
from tools.watch_minimax_phygen import remote_launcher_alive


def can_import(case,records,head):
    if type(head)is not int or case<=head:return False
    if records.get(case,{}).get('status')in ('completed','generating'):return False
    return sum(records.get(i,{}).get('status')!='completed'for i in range(head+1,case))>=2


def batch_needs_recovery(gpu,pid,alive):
    return alive is False or (gpu.get('launcher_pid')==pid and gpu.get('phase')=='failed')


def import_completed(private,base,case,records,head,batch):
    if not can_import(case,records,head):return False
    record=read(private/'cases'/f'phygen_{case:03d}'/'generation.json')
    video=private/'videos'/f'phygen_{case:03d}.mp4'
    raw=private/'cases'/f'phygen_{case:03d}'/'raw.mp4'
    if record.get('status')!='completed':return False
    if record.get('case')!=case or record.get('task_type')!='t2v' or record.get('seed')!=42 or record.get('model_revision')!='42ed227ee7df40d41602854ae760620d6eb651fe':
        raise ValueError('Unexpected private batch provenance')
    if record.get('evaluated_media',{}).get('frames')!=120 or record.get('evaluated_media',{}).get('fps')!=24:
        raise ValueError('Private batch must preserve the120-frame24FPS window')
    if sha256(video)!=record['export_sha256']or sha256(raw)!=record['raw_sha256']:
        raise ValueError('Private batch hash mismatch')
    destination=base/'cases'/f'phygen_{case:03d}';destination.mkdir(parents=True,exist_ok=True)
    target=base/'videos'/video.name;target.parent.mkdir(exist_ok=True)
    raw_temp=destination/'a800_raw.incoming';video_temp=target.with_name(target.name+'.a800.incoming')
    shutil.copyfile(raw,raw_temp);shutil.copyfile(video,video_temp)
    current={int(f.parent.name.split('_')[1]):read(f)for f in(base/'cases').glob('*/generation.json')}
    current_head=read(base/'progress.json').get('case')
    unrendered_before=[]
    if type(current_head)is int:
        unrendered_before=[i for i in range(current_head+1,case)
            if current.get(i,{}).get('status')!='completed'
            and not(base/'cases'/f'phygen_{i:03d}'/'raw.mp4').exists()]
    if not can_import(case,current,current_head)or len(unrendered_before)<2:
        raw_temp.unlink();video_temp.unlink();return False
    raw_temp.replace(destination/'raw.mp4');video_temp.replace(target)
    record.update(producer='a800-private-tail-batch',producer_batch=batch,imported_utc=utc_now())
    atomic_json(destination/'generation.json',record)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--worker',required=True)
    parser.add_argument('--interval',type=float,default=30);args=parser.parse_args()
    code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve()
    base=bundle/'baselines/minimax_h3_phygen';root=base/'backfill';root.mkdir(parents=True,exist_ok=True)
    guard=(root/'watch.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=read(root/'dispatch.json');status=root/'gpu_state.json'
    runtime=code/'vendor/minimax-h3-runtime-pinned';h3py=code.parent/'fastvideo_h3_official_clean_20260926/.venv/bin/python'
    launchpy=code/'vendor/robojudge-eval-night/detached-runtime/bin/python'
    tmp=code.parent/'.pwtmp/h3pa';tmp.mkdir(parents=True,exist_ok=True);cache=bundle/'state/h3_phygen_cache'
    env={'PYTHONPATH':str(code)+':'+str(runtime),'TMPDIR':str(tmp),'HF_HOME':str(cache/'hf'),
        'XDG_CACHE_HOME':str(cache),'TORCH_HOME':str(cache/'torch'),'CUDA_CACHE_PATH':str(cache/'cuda'),
        'TRITON_CACHE_DIR':str(cache/'triton'),'TORCHINDUCTOR_CACHE_DIR':str(cache/'inductor'),
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'8','NCCL_DEBUG':'WARN'}
    while True:
        records={int(f.parent.name.split('_')[1]):read(f)for f in(base/'cases').glob('*/generation.json')}
        completed=sum(r.get('status')=='completed'for r in records.values())
        if not read(bundle/'baselines/minimax_h3_local/backfill/complete.json').get('complete'):
            atomic_json(root/'progress.json',{'phase':'waiting_for_pcve_backfill','updated_utc':utc_now()})
            time.sleep(args.interval);continue
        batch=state.get('active_batch')
        if batch:
            private=root/f'batch_{batch["number"]:03d}'
            head=read(base/'progress.json').get('case')
            for case in batch['cases']:
                if import_completed(private,base,case,records,head,batch['number']):records[case]=read(base/'cases'/f'phygen_{case:03d}'/'generation.json')
            gpu=read(status)
            owned=gpu.get('launcher_pid')==batch['pid']
            try:alive=remote_launcher_alive(args.worker,batch['pid'],status)
            except Exception:alive=None
            if owned and gpu.get('phase')=='finished':
                state.setdefault('completed_batches',[]).append(batch);state.pop('active_batch',None)
                atomic_json(root/'dispatch.json',state);batch=None
            elif batch_needs_recovery(gpu,batch['pid'],alive):
                remaining=[i for i in batch['cases']if records.get(i,{}).get('status')not in ('completed','generating')]
                if not remaining:
                    state.setdefault('completed_batches',[]).append(batch);state.pop('active_batch',None)
                    atomic_json(root/'dispatch.json',state);continue
                if batch.get('launches',1)>=3:
                    atomic_json(root/'progress.json',{'phase':'failed','error':'Three private GPU batch launches failed; inspect its log','updated_utc':utc_now()});return
                command=batch['command'][:batch['command'].index('--cases')+1]+list(map(str,remaining))
                result=launch(args.worker,command,code,env,private/'generation.log')
                batch.update(pid=result['pid'],command=command,launches=batch.get('launches',1)+1)
                atomic_json(root/'dispatch.json',state);time.sleep(args.interval);continue
            else:
                atomic_json(root/'progress.json',{'phase':'private_batch_running','batch':batch['number'],
                    'imported':sum(records.get(i,{}).get('status')=='completed'for i in batch['cases']),
                    'total':len(batch['cases']),'updated_utc':utc_now()});time.sleep(args.interval);continue
        if completed==160:
            atomic_json(root/'complete.json',{'complete':True,'updated_utc':utc_now()});return
        primary=sorted([r for r in records.values()if r.get('status')=='completed'and not r.get('producer')],key=lambda r:r['completed_utc'])[-8:]
        slow=statistics.median(r['elapsed_seconds']for r in primary)if primary else 260
        donor=[read(f)for f in root.glob('batch_*/cases/*/generation.json')]
        durations=[r['elapsed_seconds']for r in donor if r.get('status')=='completed']
        fast=statistics.median(durations[-8:])if durations else slow
        selection_records=dict(records)
        for record in donor:
            if record.get('status')=='completed':selection_records[record['case']]={'status':'completed'}
        cases=select_tail_batch(selection_records,list(range(1,161)),slow_seconds=slow,fast_seconds=fast)
        if not cases:
            atomic_json(root/'complete.json',{'complete':True,'reason':'No sufficiently separated pending tail','updated_utc':utc_now()});return
        number=len(state.get('completed_batches',[]))+1;private=root/f'batch_{number:03d}';private.mkdir(exist_ok=True)
        command=[str(launchpy),'-u','-m','tools.run_gpu_group','--state-root',str(bundle/'state/gpu_claims'),
            '--status',str(status),'--count','8','--min-free-gib','70','--',str(h3py),'-u','-m',
            'tools.generate_minimax_phygen','--bundle',str(bundle),'--runtime',str(runtime),
            '--model-path',str(code.parent/'models/MiniMax-H3-official-fastvideo-clean'),
            '--ffmpeg',str(bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg'),
            '--num-gpus','8','--output-root',str(private),'--cases',*map(str,cases)]
        result=launch(args.worker,command,code,env,private/'generation.log')
        state['active_batch']={'number':number,'cases':cases,'pid':result['pid'],'command':command,'launches':1,'started_utc':utc_now()}
        atomic_json(root/'dispatch.json',state);time.sleep(args.interval)


if __name__=='__main__':main()
