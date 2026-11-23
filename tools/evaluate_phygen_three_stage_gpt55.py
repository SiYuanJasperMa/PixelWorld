"""Paired PhyGen three-stage GPT5.5 multiple-choice calibration.

Uses official question content; replacing probability outputs with ordinal
choices is recorded explicitly. This is not the legacy one-shot1-5 judge.
"""
import argparse
import base64
import fcntl
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import io
import json
from pathlib import Path
import os
import re
import random
import statistics
import time

import numpy as np
from PIL import Image
from openai import AzureOpenAI, OpenAI, RateLimitError

from pixelworld.util import atomic_json,sha256,utc_now


ROOT=Path(__file__).resolve().parents[1]
VERSION='phygen-gpt55-three-stage-mcq-v1'
LEGACY_JUDGE = 'gpt-5.5-2026-04-24'
GPT61_JUDGE = 'traex/GPT-6.1-Sol'
GPT61_VERSION = 'phygen-gpt61-sol-three-stage-mcq-v1'
SYSTEM_PROMPT = 'Judge only visible evidence. Return valid JSON only. Do not infer that an event happened merely because it was requested.'


class ResponsesJudge:
    """Change only the VLM transport; retain the task prompts and score rules."""

    model = GPT61_JUDGE
    version = GPT61_VERSION

    def __init__(self, client):
        self.client = client

    def complete(self, content):
        parts = []
        for item in content:
            if item['type'] == 'text':
                parts.append({'type':'input_text', 'text':item['text']})
            else:
                parts.append({'type':'input_image', 'image_url':item['image_url']['url']})
        reply = self.client.responses.create(
            model=self.model,
            input=[{'role':'system', 'content':SYSTEM_PROMPT}, {'role':'user','content':parts}],
            max_output_tokens=8192,
        )
        if reply.status != 'completed':
            raise ValueError(f'Judge response did not finish: {reply.status}')
        return reply.output_text, reply


def judge_identity(client):
    if isinstance(client, ResponsesJudge):
        return client.model, client.version
    return LEGACY_JUDGE, VERSION


def model_source(bundle, model, case):
    if model=='minimax-h3-local':
        return bundle/'baselines/minimax_h3_phygen/videos'/f'phygen_{case:03d}.mp4'
    folder='seedance25_phygen_v1_gpt55/seedance25-v1'if model=='seedance25-v1'else'phygenbench_gpt55_chronological_v2/'+model
    path=bundle/'evaluation'/folder/f'{case:03d}.json'
    if path.is_file():
        return Path(json.loads(path.read_text())['run'])
    # Closed older three-stage rows can be the authoritative adopted sources
    # for models that were never registered in the chronological-v2 cohort.
    prior=bundle/'evaluation/phygenbench_gpt55_three_stage_mcq_v1'/model/f'{case:03d}.json'
    row=json.loads(prior.read_text())
    if (row.get('version'),row.get('model'),row.get('case')) != (VERSION,model,case):
        raise ValueError('Previous score does not identify the requested adopted source')
    return Path(row['source'])


class JudgeLog(list):
    def __init__(self,cache):
        super().__init__();self.cache=cache;cache.mkdir(parents=True,exist_ok=True)


def images(source,indices):
    result={}
    if source.is_dir():
        for i in indices:result[i]=Image.open(source/'workspace/frames'/f'{i:02d}.png').convert('RGB')
    else:
        import cv2
        capture=cv2.VideoCapture(str(source));wanted=set(indices)
        try:
            for i in range(120):
                ok,rgb=capture.read()
                if not ok:raise ValueError('Video does not cover120frames')
                if i in wanted:result[i]=Image.fromarray(cv2.cvtColor(rgb,cv2.COLOR_BGR2RGB))
        finally:capture.release()
    encoded={}
    for i,rgb in result.items():
        scale=min(1.,768/max(rgb.size))
        if scale<1:rgb=rgb.resize((round(rgb.width*scale),round(rgb.height*scale)),Image.Resampling.LANCZOS)
        buf=io.BytesIO();rgb.save(buf,format='JPEG',quality=85);encoded[i]=base64.b64encode(buf.getvalue()).decode()
    return encoded


def ask(client,prompt,frames,encoded,log):
    image_hashes={str(i):hashlib.sha256(base64.b64decode(encoded[i])).hexdigest()for i in frames}
    model,version=judge_identity(client)
    cache_key=hashlib.sha256(json.dumps({'version':version,'model':model,'prompt':prompt,'frames':frames,'images':image_hashes},sort_keys=True).encode()).hexdigest()
    cached=log.cache/(cache_key+'.json')
    if cached.exists():
        record=json.loads(cached.read_text());log.append(record);return record['response']
    content=[{'type':'text','text':prompt}]
    for i in frames:
        content.append({'type':'text','text':f'Frame{i}, time{i/24:.3f}s'})
        content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+encoded[i]}})
    attempt=0
    while attempt<3:
        try:
            if isinstance(client, ResponsesJudge):
                text,response=client.complete(content)
            else:
                response=client.chat.completions.create(model=model,
                    messages=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':content}],
                    max_completion_tokens=8192)
                text=response.choices[0].message.content or ''
            match=re.search(r'\{.*\}',text or '',re.S)
            if not match:raise ValueError('Missing JSON')
            value=json.loads(match.group());record={'prompt':prompt,'frames':frames,
                'frame_image_sha256':image_hashes,'response':value,'response_id':response.id,
                'usage':response.usage.model_dump()if response.usage else None};atomic_json(cached,record);log.append(record)
            return value
        except RateLimitError as error:
            retry=error.response.headers.get('Retry-After','')if error.response is not None else''
            wait=float(retry)if retry.isdigit()else random.uniform(15,30)
            time.sleep(wait)
            continue
        except Exception:
            attempt+=1
            if attempt==3:raise
            time.sleep(3*attempt)


def retrieve(client,prompt,encoded,log):
    special={'Last Frame':119,'First Frame':0,'Middle Frame':60}
    if prompt in special:return special[prompt]
    candidates=np.linspace(0,119,32).astype(int).tolist()
    value=ask(client,'Choose the frame that best visibly depicts this retrieval description: '+prompt+
        '. Choose only from the provided frame indices. JSON: {"frame_index":integer,"reason":string}.',candidates,encoded,log)
    if value.get('frame_index')not in candidates:raise ValueError('Invalid retrieved frame')
    return value['frame_index']


def evaluate(client,source,questions,output,model,case,control=None):
    single,multi,video=questions;all_indices=sorted(set(np.linspace(0,119,32).astype(int).tolist()+
        np.linspace(0,119,26).astype(int).tolist()+[0,60,119]));encoded=images(source,all_indices)
    if control=='freeze':
        first=images(source,[0])[0];encoded={i:first for i in all_indices}
    elif control=='reverse':
        reversed_images=images(source,[119-i for i in all_indices]);encoded={i:reversed_images[119-i]for i in all_indices}
    elif control=='black':
        buf=io.BytesIO();Image.new('RGB',(768,432),'black').save(buf,format='JPEG',quality=85)
        black=base64.b64encode(buf.getvalue()).decode();encoded={i:black for i in all_indices}
    log=JudgeLog(output/model/f'calls_{case:03d}');grades=[]
    for q in single['singleimage_question']:
        frame=retrieve(client,q['Retrieval Prompt'],encoded,log)
        prompt=('Answer the original physical-phenomenon question using this frame: '+q['Question']+
            '\nStatement to evaluate: '+q['Statement']+'\nContradictory statement: '+q['Antonym']+
            '\nChoose: A=clearly contradicts the evaluated statement; B=insufficient visual evidence; '
            'C=partially shows the evaluated statement with visible inconsistency; D=clearly shows it. '
            'Do not use prior physical knowledge in place of visual evidence. '
            'JSON: {"choice":"A|B|C|D","reason":string}.')
        value=ask(client,prompt,[frame],encoded,log);grades.append({'A':0,'B':1,'C':2,'D':3}[value['choice']])
    stage1=round(statistics.fmean(grades));mid=retrieve(client,multi['multiimage_question']['Retrieval Prompt'],encoded,log)
    descriptions=multi['multiimage_question'];d1=descriptions['Description1'];d2=descriptions['Description2']
    prompt=('Check these original event-order descriptions from visible changes. '
        f'Q1: do frames0 and{mid} match "{d1}"? '
        f'Q2: do frames{mid} and119 match "{d2}"? '
        f'Q3: do all three frames match the sequence "{d1} Then {d2}"? '
        'For each choose Yes or No; insufficient evidence countsNo. '
        'JSON: {"q1":"Yes|No","q2":"Yes|No","q3":"Yes|No","reason":string}.')
    value=ask(client,prompt,sorted(set([0,mid,119])),encoded,log)
    if any(value[k]not in ['Yes','No']for k in ['q1','q2','q3']):raise ValueError('Invalid sequence choices')
    stage2=sum(value[k]=='Yes'for k in ['q1','q2','q3'])
    prompt=('Evaluate the complete video against the emphasized physical law. Prompt: '+video['caption']+
        '\nPhysical law: '+video['physical_laws']+'\nChoose the most appropriate original description. '
        'Do not choose Almost Realistic unless supported by the complete visible sequence.\n'+
        '\n'.join(f'{i} ({label}): '+video['video_question'][f'Description{i}']for i,label in enumerate(
            ['Completely Fantastical','Highly Unrealistic','Slightly Unrealistic','Almost Realistic'],1))+
        '\nJSON: {"choice":integer1to4,"reason":string}.')
    value=ask(client,prompt,np.linspace(0,119,26).astype(int).tolist(),encoded,log)
    if type(value['choice'])is not int or not 1<=value['choice']<=4:raise ValueError('Invalid naturalness choice')
    stage3=value['choice']-1;overall=round((stage1+stage2+stage3)/3)/3
    judge,version=judge_identity(client)
    record={'version':version,'model':model,'case':case,'source':str(source),'stage_scores':[stage1,stage2,stage3],
        'overall':overall,'question_grades':grades,'judge':judge,'caption':video['caption'],
        'finished_utc':utc_now(),'calls':log,'probability_metric_equivalence':False,'calibration_only':True,'control':control}
    atomic_json(output/model/f'{case:03d}.json',record);return record


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--cases',type=int,nargs='+',default=list(range(1,161)));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=8)
    p.add_argument('--models',nargs='+',default=['seedance25-v1','gpt61-sol','gpt56-sol','gpt56-luna','gpt56-terra'])
    p.add_argument('--judge',choices=['gpt55','gpt61-sol'],default='gpt55')
    p.add_argument('--responses-base-url',help='Responses endpoint for gpt61-sol; key from PIXELWORLD_JUDGE_API_KEY')
    args=p.parse_args()
    if (args.workers < 1 or len(set(args.cases)) != len(args.cases) or not all(1 <= c <= 160 for c in args.cases)
        or len(set(args.models)) != len(args.models)):
        p.error('Use distinct official case IDs, distinct models, and at least one worker')
    args.cases.sort()
    b=args.bundle.resolve();qroot=ROOT/'vendor/phygenbench-official-source/PhyGenBench'
    sets=[json.loads((qroot/n).read_text())for n in ['single_question.json','multi_question.json','video_question.json']]
    if args.judge == 'gpt61-sol':
        key=os.environ.get('PIXELWORLD_JUDGE_API_KEY')
        if not args.responses_base_url or not key:
            p.error('gpt61-sol requires --responses-base-url and PIXELWORLD_JUDGE_API_KEY')
        client=ResponsesJudge(OpenAI(api_key=key,base_url=args.responses_base_url,timeout=180,max_retries=0))
    else:
        if args.responses_base_url:
            p.error('--responses-base-url is only valid for gpt61-sol')
        client=AzureOpenAI(api_key=(b/'state/gpt55.key').read_text().strip(),api_version='2023-07-01-preview',
            azure_endpoint='https://search.bytedance.net/gpt/openapi/online/multimodal/crawl',timeout=180,max_retries=0)
    judge,version=judge_identity(client)
    rows=[];jobs=[];failures=[]
    for case in args.cases:
        sources={}
        for model in args.models:
            sources[model]=model_source(b,model,case)
        for model,source in sources.items():jobs.append((case,model,source))
    args.output.mkdir(parents=True,exist_ok=True)
    def save(phase, reused):
        model_scores={}
        for model in args.models:
            scores=[r['overall']for r in rows if r['model']==model]
            if scores:
                model_scores[model]={'cases':len(scores),'mean':statistics.fmean(scores)}
        status={'phase':phase,'completed_jobs':len(rows),'expected_jobs':len(jobs),
                'reused_jobs':reused,'failures':failures}
        atomic_json(args.output/'summary.json',{**status,'version':version,'calibration_only':True,'cases':args.cases,
            'judge':judge,'expected_cases_per_model':len(args.cases),
            'full160':set(args.cases)==set(range(1,161)),'models':model_scores})
        if phase != 'running':
            status['finished_utc']=utc_now()
        atomic_json(args.output/'execution.json',status)

    def work(job):
        case,model,source=job
        return evaluate(client,source,[s[case-1]for s in sets],args.output,model,case)

    with (args.output/'.judge.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pending=[]
        for case,model,source in jobs:
            target=args.output/model/f'{case:03d}.json'
            if not target.is_file():
                pending.append((case,model,source));continue
            row=json.loads(target.read_text())
            if (row.get('version'),row.get('judge'),row.get('source'),row.get('model'),row.get('case')) != (
                version,judge,str(source),model,case
            ) or len(row.get('stage_scores',[])) != 3:
                raise ValueError('Existing score uses a different judge, protocol, case, or source; use a separate output directory')
            rows.append(row)
        reused=len(rows)
        save('running',reused)
        with ThreadPoolExecutor(max_workers=args.workers)as pool:
            futures={pool.submit(work,job):job for job in pending}
            for future in as_completed(futures):
                case,model,_=futures[future]
                try:
                    row=future.result()
                except Exception as e:
                    failures.append({'model':model,'case':case,'error':str(e)[-1600:]})
                    print(model,case,'failed',str(e)[-300:],flush=True)
                else:
                    rows.append(row);print(model,case,row['stage_scores'],row['overall'],flush=True)
                save('running',reused)
        save('failed' if failures else 'finished',reused)


if __name__=='__main__':main()
