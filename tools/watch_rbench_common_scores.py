"""Score closed valid RBench candidates using the original Common Manipulation scorer."""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import time

from pixelworld.config import load_config
from pixelworld.supervisor import inspect
from pixelworld.retries import infrastructure_failure
from pixelworld.util import atomic_json, sha256, utc_now
from tools.evaluate_rbench_common_gpt61 import GPT61_JUDGE, UPSTREAM, OfficialGPTTransport, load_official

PROTOCOL = 'rbench-common-official-gpt-branch-gpt61-sol-v1'


def manifest_info(run: Path, case: str, key: str, track: str = 'vision-agent') -> dict:
    m = json.loads((run/'run_manifest.json').read_text())
    spec = m['media_contract']
    if not (m.get('status')=='complete' and m['validation'].get('pass') is True and
            m['case_id']==case and m['model_key']==key and type(m['attempt']) is int and m['attempt']>=1 and
            m.get('track', 'vision-agent') == track and
            spec['frames']==120 and spec['fps']==24 and spec['task_type']=='i2v'):
        raise ValueError('Not the verified first-and-only RBench candidate')
    for ordinal in range(1, m['attempt']):
        prior = run.parent / f'case_{case}_attempt_{ordinal:02d}' / 'run_manifest.json'
        if not prior.is_file():
            raise ValueError('Missing predecessor for an infrastructure retry')
        record = json.loads(prior.read_text())
        authorization=m.get('retry_authorization')or{}
        owner_recovery=(authorization.get('exception')=='near_complete_rescue'
                        and authorization.get('until_first_delivery') is True)
        if (record.get('case_id') != case or record.get('model_key') != key
                or record.get('status') != 'failed'
                or not (infrastructure_failure(record) or owner_recovery)):
            raise ValueError('RBench cannot reroll a real model attempt')
    return m


def export_video(run: Path, out: Path, ffmpeg: str) -> None:
    frame_dir = run/'workspace/frames'
    if any(not (frame_dir/f'{i:02d}.png').is_file() for i in range(120)):
        raise ValueError('Accepted candidate is missing decoded frames')
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem+'.part.mp4')
    try:
        subprocess.run([ffmpeg,'-nostdin','-loglevel','error','-y','-framerate','24','-start_number','0',
                        '-i',str(frame_dir/'%02d.png'),'-frames:v','120','-an','-c:v','libx264rgb','-crf','0',str(tmp)],check=True)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


def numeric_score(official, response):
    s = official.compute_final_score(response)
    if not isinstance(s,(int,float)) or isinstance(s,bool) or not math.isfinite(s) or not 1<=s<=5:
        raise ValueError('Official scorer did not return a finite 1–5 score')
    return s


def read_pilot(official, pilot, prompt_path, run, key, case):
    """Reuse the already scored unchanged pilot without issuing another VLM call."""
    cfg = pilot/'rbench_case_0001.json'
    if key!='gpt61-sol' or case!='0001' or not cfg.is_file():return None
    config=json.loads(cfg.read_text())
    prior=Path(config['runs_root'])/key/'vision-agent/case_0001_attempt_01'
    if prior.resolve()!=run.resolve():return None
    meta=json.loads((pilot/'evaluation/summary.json').read_text())
    if (meta.get('protocol'),meta.get('official_code_commit'),meta.get('judge'),meta.get('manifest_sha256')) != (PROTOCOL,UPSTREAM,GPT61_JUDGE,sha256(prompt_path)) or meta['failed_cases'] or meta['scored_cases']!=1:
        return None
    with (pilot/'evaluation/results.csv').open(newline='') as f:rows=list(csv.DictReader(f))
    if len(rows)!=1 or rows[0]['name']!='common_manipulation_0001':return None
    return {'score':numeric_score(official,read_upstream_details(rows[0]['details'])),
            'reused_pilot_metrics':str(pilot/'evaluation/results.csv'),
            'video_sha256':sha256(pilot/'videos/0001.mp4')}


def read_upstream_details(value):
    # Upstream writes dicts to CSV using their Python representation.
    import ast
    try:return json.loads(value)
    except ValueError:return ast.literal_eval(value)


def configs(dispatch):
    main=json.loads((dispatch/'launch_receipt.json').read_text())['rows']
    optional=dispatch/'remaining_roster_queue/queued.json'
    extra=json.loads(optional.read_text()) if optional.is_file() else []
    rows=[(r['model'],Path(r['config'])) for r in main+extra]; result={}
    override_path=dispatch/'generation_source_overrides.json'
    overrides=json.loads(override_path.read_text()) if override_path.is_file() else {}
    for key,path in rows:
        if key in result or not path.resolve().is_relative_to(dispatch.resolve()):raise ValueError('Unexpected dispatch identity or path')
        c=load_config(path)
        if c.max_total_attempts!=1 or c.max_attempts!=1 or list(c.case_ids)!=[f'{i:04}'for i in range(1,51)]:
            raise ValueError('Only exact 50-case, one-candidate RBench cohort configs supported')
        if key in overrides:
            override=overrides[key]
            if (override.get('owner_authorized_recovery') is not True
                    or Path(override.get('source_config','')).resolve()!=path.resolve()):
                raise ValueError('Unverified RBench recovery source override')
            replacement=load_config(Path(override['recovery_config']))
            old,new=c.model(key),replacement.model(key)
            if (replacement.protocol!=c.protocol or replacement.case_ids!=c.case_ids
                    or replacement.inputs_root.resolve()!=c.inputs_root.resolve()
                    or replacement.max_attempts!=1 or replacement.max_total_attempts!=1
                    or (new.model,new.cli_backend,new.reasoning_effort,new.tracks)!=
                       (old.model,old.cli_backend,old.reasoning_effort,old.tracks)):
                raise ValueError('Recovery changes the RBench input or model protocol')
            c=replacement
        result[key]=(c,c.model(key))
    return result


def main():
    from openai import OpenAI
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dispatch',type=Path,required=True);p.add_argument('--pilot',type=Path,required=True)
    p.add_argument('--official-repo',type=Path,required=True);p.add_argument('--prompts',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--base-url',required=True);p.add_argument('--ffmpeg',required=True)
    p.add_argument('--poll-seconds',type=int,default=45);p.add_argument('--once',action='store_true');a=p.parse_args()
    key=os.environ.get('PIXELWORLD_JUDGE_API_KEY');assert key and a.poll_seconds>=1
    prompts=json.loads(a.prompts.read_text());assert len(prompts)==50 and all(p['name']==f'common_manipulation_{i:04}'for i,p in enumerate(prompts,1))
    os.umask(0o077);a.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    official,script=load_official(a.official_repo);client=OpenAI(api_key=key,base_url=a.base_url,timeout=180,max_retries=0)
    with (a.output/'watcher.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            models={}
            for model,(config,entry) in configs(a.dispatch).items():
                root=a.output/model;rows=[];awaiting=[];generation_failures=[];judge_errors=[]
                for case in config.case_ids:
                    info=inspect(config,entry,'vision-agent',case)
                    if info['state']=='exhausted':generation_failures.append(case);continue
                    if info['state']!='complete':awaiting.append(case);continue
                    run=Path(info['run']);target=root/'results'/f'{case}.json';m=manifest_info(run,case,model);identity=sha256(run/'run_manifest.json')
                    if target.is_file():
                        row=json.loads(target.read_text());assert row['source_manifest_sha256']==identity and row['protocol']==PROTOCOL;rows.append(row);continue
                    err=root/'judge_errors'/f'{case}.json'
                    if err.is_file():
                        old=json.loads(err.read_text())
                        if old['attempts']>=3:
                            judge_errors.append(case);continue
                        if time.time()-old['unix']<60:
                            awaiting.append(case);continue
                    try:
                        proof=read_pilot(official,a.pilot,a.prompts,run,model,case)
                        if not proof:
                            vid=root/'videos'/f'{case}.mp4';export_video(run,vid,a.ffmpeg)
                            grids=official.Video_preprocess().convert_video_to_grid(str(vid),num_image=6)
                            raw=root/'api_originals';raw.mkdir(parents=True,exist_ok=True)
                            transport=OfficialGPTTransport(client,raw);official.OpenAI=lambda **_:transport
                            result=official.process_single_image_gpt((f'{case}.jpg',prompts,grids,None))
                            proof={'score':numeric_score(official,result['response']),'video_sha256':sha256(vid),'response':result['response']}
                        row={'protocol':PROTOCOL,'model':model,'case':case,'judge':GPT61_JUDGE,'source_run':str(run),'source_manifest_sha256':identity,
                             'scorer_sha256':sha256(script),'generation_attempt':m['attempt'],'finished_utc':utc_now(),**proof};atomic_json(target,row);rows.append(row)
                    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as error:
                        attempts=json.loads(err.read_text())['attempts'] if err.is_file() else 0
                        atomic_json(err,{'attempts':attempts+1,'unix':time.time(),'error':str(error)[-500:]});judge_errors.append(case)
                all_cases=not awaiting and not generation_failures and not judge_errors and len(rows)==50
                models[model]={'scored':len(rows),'expected':50,'generation_failures':generation_failures,'waiting':awaiting,
                               'judge_errors':judge_errors,'full_common50':all_cases,'mean':sum(r['score'] for r in rows)/50 if all_cases else None}
            atomic_json(a.output/'execution.json',{'protocol':PROTOCOL,'judge':GPT61_JUDGE,'models':models,'updated_utc':utc_now()})
            if a.once:return
            time.sleep(max(a.poll_seconds,1))

if __name__=='__main__':main()
