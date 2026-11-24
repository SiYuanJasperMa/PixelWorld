"""Observe actual run process trees; no sampling, killing, or model feedback."""
from __future__ import annotations
import argparse, json, os, time
from pathlib import Path

import psutil


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def sample(run):
    try:
        pid = int((run/'logs/supervisor.pid').read_text().strip())
        parent = psutil.Process(pid)
        args = parent.cmdline()
        if '--case' not in args or '--attempt' not in args:
            return None
        manifest = json.loads((run/'run_manifest.json').read_text())
        if args[args.index('--case')+1] != manifest['case_id']:
            return None
        processes = [parent] + parent.children(recursive=True)
        rss = vms = cpu = 0; live = []
        for process in processes:
            try:
                info = process.memory_info(); times = process.cpu_times()
                rss += info.rss; vms += info.vms; cpu += times.user + times.system
                live.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {'root_pid':pid,'root_create_time':parent.create_time(),'pids':live,
                'tree_rss_bytes':rss,'tree_vms_bytes':vms,'tree_cpu_seconds':cpu,
                'model':manifest['model_key'],'track':manifest['track'],'case_id':manifest['case_id'],
                'attempt':manifest['attempt'],'hostname':os.uname().nodename,'observed_unix':time.time()}
    except (OSError, ValueError, KeyError, psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def run(args):
    stats={}; output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    while not (output/'STOP').exists():
        for model in args.runs_root.iterdir():
            if not model.is_dir() or model.name.startswith('_'):continue
            for track in model.iterdir():
                if not track.is_dir():continue
                for directory in track.iterdir():
                    if not directory.is_dir() or not directory.name.startswith('case_'):continue
                    observation=sample(directory)
                    if observation is None:continue
                    key=str(directory.relative_to(args.runs_root))
                    previous=stats.get(key)
                    if previous and previous['root_create_time']!=observation['root_create_time']:
                        key+='@'+str(observation['root_create_time'])
                        previous=stats.get(key)
                    value=previous or {'root_create_time':observation['root_create_time'],'samples':0,
                                       'peak_tree_rss_bytes':0,'first_observed_unix':observation['observed_unix']}
                    value.update(samples=value['samples']+1,
                                 peak_tree_rss_bytes=max(value['peak_tree_rss_bytes'],observation['tree_rss_bytes']),
                                 last=observation)
                    stats[key]=value
        save(output/'resources.json',{'observer_pid':os.getpid(),'hostname':os.uname().nodename,
              'updated_unix':time.time(),'runs':stats,'note':'Sampled sum of process RSS; shared pages may be counted twice. Peak can miss sub-interval spikes. Host memory is not a cgroup allocation.'})
        if args.once:return
        time.sleep(args.interval)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runs-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--interval',type=float,default=1)
    p.add_argument('--once',action='store_true');run(p.parse_args())
