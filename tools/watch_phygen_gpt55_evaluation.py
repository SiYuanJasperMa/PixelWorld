"""Score newly complete PhyGenBench cohorts with the shared GPT-5.5 judge."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

from pixelworld.config import load_config
from pixelworld.util import atomic_json, utc_now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = load_config(args.config)
    output = args.output.resolve()
    if output.is_relative_to(config.runs_root) or output.is_relative_to(config.inputs_root):
        raise ValueError('Judge output must stay outside generation inputs/runs')
    if len(config.case_ids or ()) != 160 or args.workers < 1 or args.interval <= 0:
        raise ValueError('Requires the complete 160-prompt cohort and positive limits')
    output.mkdir(parents=True, exist_ok=True)
    lock = (output/'scheduler.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    code = Path(__file__).resolve().parents[1]
    while True:
        generation = json.loads((config.runs_root/'status.json').read_text())
        summary_path = output/'summary.json'
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        ready = [m.key for m in config.models if 'vision-agent' in m.tracks and
                 generation.get('groups',{}).get(m.key+'/vision-agent',{}).get('counts',{}).get('complete') == 160]
        pending = [key for key in ready if summary.get('models',{}).get(key,{}).get('cases') != 160]
        cohorts = {m.key: {'phase':'complete' if summary.get('models',{}).get(m.key,{}).get('cases') == 160
                           else 'waiting_for_generation',
                           'accepted':generation.get('groups',{}).get(m.key+'/vision-agent',{}).get('counts',{}).get('complete',0)}
                   for m in config.models}
        state = {'updated_utc':utc_now(),'pid':__import__('os').getpid(),'current_models':pending,'cohorts':cohorts}
        if pending:
            for key in pending:cohorts[key]['phase']='scoring'
            # Include all ready models so the shared summary retains earlier scores.
            command = [sys.executable,'-u','-m','tools.evaluate_phygen_gpt55',
                '--generations',str(config.runs_root),'--output',str(output),'--workers',str(args.workers),
                '--models',*ready]
            with (output/'watcher.log').open('ab') as log:
                child=subprocess.Popen(command,cwd=code,stdout=log,stderr=subprocess.STDOUT)
                state['judge_pid']=child.pid
                atomic_json(output/'scheduler.json',state)
                returncode=child.wait()
            state.update(returncode=returncode,updated_utc=utc_now())
            for key in pending:cohorts[key]['phase']='finished' if returncode == 0 else 'retry_pending'
        atomic_json(output/'scheduler.json',state)
        print({'ready':ready,'new_cohorts':pending,'returncode':state.get('returncode')},flush=True)
        if args.once:return
        time.sleep(args.interval)


if __name__=='__main__':main()
