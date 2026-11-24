"""Judge RBench appearance once each official case has a stable completed roster."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import time

from openai import OpenAI

from pixelworld.supervisor import inspect
from pixelworld.util import atomic_json, sha256, utc_now
from tools.evaluate_video_photographic_gpt61 import (
    GPT61_JUDGE, RUBRIC_SHA256, VERSION, evaluate, summarize,
)
from tools.watch_rbench_common_scores import configs, manifest_info

BENCHMARK = 'rbench_common_manipulation'
CASES = tuple(f'{i:04d}' for i in range(1,51))


def closed(states):
    return bool(states) and all(row['state'] in ('complete','exhausted') for row in states.values())


def frozen_case(case, states):
    videos, failed = [], []
    for model, info in sorted(states.items()):
        if info['state'] == 'exhausted':
            failed.append(model)
            continue
        source=Path(info['run'])
        m=manifest_info(source,case,model)
        videos.append({'benchmark':BENCHMARK,'case':case,'model':model,'source':str(source),
                       'identity':sha256(source/'run_manifest.json'),'frames':m['media_contract']['frames'],
                       'fps':m['media_contract']['fps']})
    return {'case':case,'videos':videos,'generation_failures':failed,'rubric_sha256':RUBRIC_SHA256,
            'judge':GPT61_JUDGE,'protocol':VERSION}


def step(roster, output, client, limit):
    rows=[]; cases=[]; errors=[]; attempted=0
    for case in CASES:
        states={m:inspect(c,entry,'vision-agent',case)for m,(c,entry)in roster.items()}
        if not closed(states):
            cases.append({'case':case,'state':'waiting_for_generation'});continue
        frozen=output/'case_inputs'/f'{case}.json'
        wanted=frozen_case(case,states)
        if frozen.is_file() and json.loads(frozen.read_text())!=wanted:
            cases.append({'case':case,'state':'source_changed_requires_review'});continue
        if not frozen.is_file():
            atomic_json(frozen,wanted)
        clips=wanted['videos']
        if not clips:
            cases.append({'case':case,'state':'no_delivery','generation_failures':wanted['generation_failures']});continue
        scored=0
        for trial in (0,1):
            target=output/'results'/BENCHMARK/f'{case}_{trial}.json'
            if not target.is_file():
                if attempted>=limit:
                    continue
                attempted+=1
            try:
                rows.append(evaluate(client,BENCHMARK,case,trial,clips,output));scored+=1
            except Exception as error:
                errors.append({'case':case,'trial':trial,'error_type':type(error).__name__})
        cases.append({'case':case,'state':'scored'if scored==2 else 'waiting_for_appearance',
                      'scored_orders':scored,'models':[r['model']for r in clips],
                      'generation_failures':wanted['generation_failures']})
    result={'benchmark':BENCHMARK,'judge':GPT61_JUDGE,'version':VERSION,'rubric_sha256':RUBRIC_SHA256,
            'roster':sorted(roster),'expected_cases':50,'cases':cases,'completed_calls':len(rows),
            'generation_roster_closed':all(r['state']!='waiting_for_generation'for r in cases),
            'finished':all(r['state']in ('scored','no_delivery')for r in cases),
            'failures':errors,**summarize(rows),'updated_utc':utc_now()}
    atomic_json(output/'execution.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dispatch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--base-url',required=True)
    p.add_argument('--limit-per-cycle',type=int,default=4)
    p.add_argument('--poll-seconds',type=int,default=90)
    p.add_argument('--once',action='store_true')
    a=p.parse_args()
    key=os.environ.get('PIXELWORLD_JUDGE_API_KEY')
    if not key or a.limit_per_cycle<1 or a.poll_seconds<1:
        p.error('Pass a private judge credential and positive cycle limits')
    roster=configs(a.dispatch)
    if len(roster)!=8:
        raise ValueError('RBench requires the registered eight-model roster')
    os.umask(0o077);a.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    client=OpenAI(api_key=key,base_url=a.base_url,timeout=240,max_retries=0)
    with (a.output/'watcher.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            result=step(roster,a.output,client,a.limit_per_cycle)
            print(json.dumps({'scored_cases':sum(x['state']=='scored'for x in result['cases']),
                              'completed_calls':result['completed_calls'],'failures':len(result['failures']),
                              'finished':result['finished']}),flush=True)
            if result['finished'] or a.once:
                return
            time.sleep(a.poll_seconds)


if __name__=='__main__':main()
