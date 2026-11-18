"""Dispatch full PCVE main/Text cohorts without duplicating existing GPU jobs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import shlex
import subprocess
import time

from pixelworld.config import load_config
from pixelworld.util import atomic_json, utc_now
from tools.live_evaluation_data import pcve_score, read


PROBE = '''import json,sys
from pathlib import Path
root=sys.argv[1];rows=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  if (p/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':continue
  argv=[x.decode(errors='replace')for x in(p/'cmdline').read_bytes().split(b'\\0')if x]
  names=('pcve_worker_start.py','tools.run_pcve_gpu_worker','run_pcve_gpu_worker.py',
         'tools.evaluate_pcve_full','evaluate_pcve_full.py')
  if root in ' '.join(argv) and any(x in names or Path(x).name in names for x in argv):rows.append([int(p.name),argv])
 except OSError:pass
print(json.dumps(rows))
'''


def probe(worker, code):
    command = ['ssh', '-p', '22222', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', worker,
               'python3 -c ' + shlex.quote(PROBE) + ' ' + shlex.quote(str(code))]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=25)
        return {'worker': worker, 'verified': True, 'commands': json.loads(result.stdout)}
    except (subprocess.SubprocessError, ValueError) as error:
        return {'worker': worker, 'verified': False, 'commands': [], 'error': type(error).__name__}


def identity(argv, main_config):
    if any(Path(x).name == 'pcve_worker_start.py' for x in argv):
        return (str(main_config), argv[-1], 'vision-agent')
    if '--config' in argv and '--model' in argv and '--track' in argv:
        return tuple(argv[argv.index(key) + 1] for key in ('--config', '--model', '--track'))
    return None


def ready_jobs(bundle):
    jobs = []
    for name, folder in [('pcve_full.json', 'pcve_standard720p24fps_scheduler'),
                         ('pcve_text_analysis.json', 'pcve_text_scheduler')]:
        path = bundle / 'configs' / name
        if not path.is_file():
            continue
        config = load_config(path)
        if len(config.case_ids or ()) != 129:
            raise ValueError('PCVE automatic scoring requires the complete 129-case configuration')
        groups = read(config.runs_root / 'status.json').get('groups', {})
        for model in config.models:
            for track in model.tracks:
                counts = groups.get(model.key + '/' + track, {}).get('counts', {})
                output = bundle / 'evaluation' / folder / model.key / track
                if (counts.get('complete') == 129 and not counts.get('exhausted', 0)
                        and not pcve_score(read(output / 'summary.json'), model.key, track)):
                    jobs.append({'key': (str(path), model.key, track), 'config': path,
                                 'model': model.key, 'track': track, 'output': output})
    return jobs


def choose(jobs, probes, main_config, limit, previous=None):
    # An unreachable assigned worker may still be evaluating. Do not duplicate it.
    if not all(row['verified'] for row in probes):
        return []
    active = {identity(argv, main_config) for row in probes for _, argv in row['commands']}
    active.discard(None)
    loads = {row['worker']: len({identity(argv, main_config) for _, argv in row['commands']} - {None})
             for row in probes}
    selected = []
    for job in jobs:
        if job['key'] in active:
            continue
        before = (previous or {}).get('|'.join(job['key']), {})
        if before.get('retry_after_unix', 0) > time.time():
            continue
        worker = min(loads, key=loads.get)
        if loads[worker] >= limit:
            break
        selected.append((worker, job))
        loads[worker] += 1
        active.add(job['key'])
    return selected


def launch(worker, job, code, bundle, python, ffmpeg):
    output = job['output']; output.mkdir(parents=True, exist_ok=True)
    command = [str(python), '-u', '-m', 'tools.run_pcve_gpu_worker', '--bundle', str(bundle),
               '--config', str(job['config']), '--model', job['model'], '--track', job['track'],
               '--output', str(output), '--python', str(python), '--ffmpeg', str(ffmpeg)]
    script = ('import os,subprocess,json; os.chdir(' + repr(str(code)) + '); '
              'log=open(' + repr(str(output / 'dispatch.log')) + ',"ab"); '
              'p=subprocess.Popen(' + repr(command) + ',stdin=subprocess.DEVNULL,stdout=log,'
              'stderr=subprocess.STDOUT,start_new_session=True); print(json.dumps({"pid":p.pid}))')
    result = subprocess.run(['ssh', '-p', '22222', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             worker, 'python3 -c ' + shlex.quote(script)],
                            text=True, capture_output=True, timeout=25, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--worker', action='append', required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--max-jobs-per-worker', type=int, default=2)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.max_jobs_per_worker < 1 or args.interval < 1:
        parser.error('Positive worker capacity and interval required')
    bundle = args.bundle.resolve(); code = Path(__file__).resolve().parents[1]
    state_path = bundle / 'state/pcve_gpu_dispatch.json'
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not (bundle / 'evaluation/pcve_standard720p24fps_scheduler/STOP').exists():
            with ThreadPoolExecutor(max_workers=len(args.worker)) as pool:
                probes = list(pool.map(lambda worker: probe(worker, code), args.worker))
            jobs = ready_jobs(bundle)
            previous = read(state_path).get('jobs', {})
            selected = choose(jobs, probes, bundle / 'configs/pcve_full.json',
                              args.max_jobs_per_worker, previous)
            records = dict(previous)
            for worker, job in selected:
                key = '|'.join(job['key'])
                try:
                    result = launch(worker, job, code, bundle, args.python.absolute(), args.ffmpeg.absolute())
                    records[key] = {'phase': 'launched', 'worker': worker, **result,
                                    'retry_after_unix': time.time() + 600}
                except (subprocess.SubprocessError, ValueError) as error:
                    records[key] = {'phase': 'dispatch_stale', 'worker': worker,
                                    'error': type(error).__name__, 'retry_after_unix': time.time() + 60}
            atomic_json(state_path, {'updated_utc': utc_now(), 'workers': probes, 'jobs': records,
                                    'ready_unscored': len(jobs), 'process_scope_verified': all(p['verified'] for p in probes)})
            print({'updated_utc': utc_now(), 'ready_unscored': len(jobs), 'launched': len(selected)}, flush=True)
            if args.once:
                return
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
