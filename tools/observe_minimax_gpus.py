"""Record bounded GPU-utilization and delivery history for the three H3 workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import subprocess
import time

from pixelworld.util import atomic_json,utc_now
from tools.watch_minimax_pcve import read


WORKERS=('19ebb06cd438f7ff','5e5bed85e489ddcb','0617be31d4d61ee1')


def effective_utilization(gpu):
    return 0.0 if gpu.get('keeper_only') else float(gpu['utilization_gpu'])


def probe(worker):
    script='''import subprocess,json,socket
from tools.run_gpu_evaluation import gpu_app_snapshot,known_keepalive_devices
apps=gpu_app_snapshot()or{};keepers=known_keepalive_devices();rows=[]
r=subprocess.run(['nvidia-smi','--query-gpu=index,memory.total,memory.used,utilization.gpu,utilization.memory,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True)
for line in r.stdout.splitlines():
 a=[x.strip()for x in line.split(',')];i=int(a[0]);info=apps.get(i,{})
 rows.append({'index':i,'memory_total_mib':int(a[1]),'memory_used_mib':int(a[2]),'utilization_gpu':float(a[3]),'utilization_memory':float(a[4]),'power_watts':float(a[5])if a[5]!='[N/A]'else None,'keeper_only':i in keepers and info.get('apps')==1 and info.get('used_mib',2048)<=1024})
print(json.dumps({'hostname':socket.gethostname(),'gpus':rows}))
'''
    try:
        result=subprocess.run(['ssh','-p','22222','-o','BatchMode=yes','-o','ConnectTimeout=10',
            worker+'.worker_0.job.masiyuan-jasper@merlin-ssh-proxy-cn.byted.org',
            'cd '+shlex.quote(str(Path(__file__).resolve().parents[1]))+' && python -c '+shlex.quote(script)],
            capture_output=True,text=True,timeout=40,check=True)
        return {'worker':worker,'verified':True,**json.loads(result.stdout)}
    except(subprocess.SubprocessError,ValueError)as error:
        return {'worker':worker,'verified':False,'error':type(error).__name__}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--interval',type=float,default=180)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args();bundle=args.bundle.resolve();root=bundle/'state/minimax_gpu_watch'
    root.mkdir(parents=True,exist_ok=True);history=read(root/'samples.json').get('samples',[])
    while True:
        with ThreadPoolExecutor(max_workers=3)as pool:workers=list(pool.map(probe,WORKERS))
        progress={key:read(bundle/'baselines'/folder/'progress.json')for key,folder in
            [('pcve','minimax_h3_local'),('phygenbench','minimax_h3_phygen')]}
        for key,folder in [('pcve','minimax_h3_local'),('phygenbench','minimax_h3_phygen')]:
            progress[key]['completed']=sum(read(f).get('status')=='completed'
                for f in(bundle/'baselines'/folder/'cases').glob('*/generation.json'))
        current={'utc':utc_now(),'unix':time.time(),'workers':workers,
            'benchmarks':{key:{k:value.get(k)for k in ('phase','completed','total','failed_cases','case')}
                for key,value in progress.items()}}
        for worker in workers:
            if worker['verified']:
                worker['mean_effective_gpu_utilization']=sum(effective_utilization(g)for g in worker['gpus'])/len(worker['gpus'])
                worker['model_resident_gpus']=sum(not g['keeper_only']and g['memory_used_mib']>1024 for g in worker['gpus'])
        history=(history+[current])[-480:]
        atomic_json(root/'latest.json',current);atomic_json(root/'samples.json',{'samples':history,
            'note':'One sample every180 seconds; keepalive-only devices count as zero model utilization. Temporary stage waits require inspection, not blind process duplication.'})
        print(json.dumps({'utc':current['utc'],'completed':{k:v.get('completed')for k,v in progress.items()},
            'verified_workers':sum(w['verified']for w in workers)}),flush=True)
        if all(v.get('phase')=='finished'for v in progress.values()):
            atomic_json(root/'complete.json',{'complete':True,'utc':utc_now()});return
        if args.once:return
        time.sleep(args.interval)


if __name__=='__main__':main()
