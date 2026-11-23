"""Blind photographic-appearance judgments for the selected main-table cohorts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import statistics
import time

import numpy as np
from openai import APIConnectionError, OpenAI, RateLimitError
from PIL import Image

from pixelworld.util import atomic_json, sha256, utc_now
from tools.evaluate_phygen_three_stage_gpt55 import GPT61_JUDGE

VERSION = 'photographic-appearance-gpt61-sol-v1'
RUBRIC = '''Assess PHOTOGRAPHIC APPEARANCE ONLY for these anonymous clips. All clips may be synthesized. The question is whether their depicted scene looks like it could have been filmed by a real camera, regardless of how it was made.
Ignore physical-law correctness, action completion, prompt following, and whether the motion is possible. Do not infer a model name or source. Do not reward an image just for being sharp, clean, smooth or free of noise. Natural depth of field, ordinary camera blur, grain and imperfect lighting do not themselves make footage unphotographic. Rendered imagery may score highly if its visible appearance is convincingly photographic.
Use visible evidence across the sampled frames: natural material microtexture and nonuniformity; believable object surfaces and fine detail; photographic illumination, reflections and shadows; believable scene geometry and depth; overall camera-like appearance rather than diagram, illustration or visibly synthetic graphics. No single cue, such as blur or clean edges, is sufficient.
Rate each clip on this anchored scale:
1: Clearly an illustration, diagram, cartoon or crude synthetic depiction, with little camera-like appearance.
2: Clearly stylized or synthetic-looking scene; most materials, details or lighting lack photographic appearance.
3: Partly photographic-looking, but substantial visibly artificial surfaces, geometry, texture or illumination remain.
4: Mostly convincing photographic appearance, with a few visible synthetic-looking artifacts.
5: Convincingly photographic appearance across the scene and sampled frames; no prominent visible synthetic-looking cues.
If the clip provides no discernible scene evidence, use null and explain why; do not mistake an empty black screen for realism.
For each unordered pair, also choose which clip has more convincing photographic appearance, or tie when the evidence does not distinguish them. This pairwise judgment is about appearance, not physical correctness or technical sharpness.
Return JSON only: {"scores":[{"id":"A","score":1,"evidence":["specific visible cue with frame index"]}],"pairs":[{"left":"A","right":"B","winner":"A|B|tie","evidence":"specific visible appearance difference"}]}. Include every provided clip exactly once and every unordered pair exactly once. Scores must be integers1..5 or null. Never justify a score by provenance, brand, presumed generation technique or task success.'''
SYSTEM = 'Judge only the requested visible photographic appearance. Model identities and task captions are intentionally withheld. Return valid JSON.'
RUBRIC_SHA256 = hashlib.sha256(RUBRIC.encode()).hexdigest()
SLUG = re.compile(r'[A-Za-z0-9_-]+$')


@lru_cache(maxsize=32)
def sample_frames(source: str, frames: int, fps: float):
    """Eight uniform samples, the original 768px/JPEG85 appearance inputs."""
    path = Path(source)
    cap = None
    if path.is_file():
        import cv2
        cap = cv2.VideoCapture(str(path))
        if int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) < frames:
            cap.release(); raise ValueError('Input video is shorter than its recorded evaluation scope')
    result = []
    try:
        for i in np.linspace(0, frames - 1, 8).astype(int).tolist():
            if cap is None:
                with Image.open(path/'workspace/frames'/f'{i:02d}.png') as image:
                    rgb = image.convert('RGB')
            else:
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, frame = cap.read()
                if not ok:
                    raise ValueError(f'Cannot decode frame {i}')
                rgb = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            scale = min(1., 768/max(rgb.size))
            if scale < 1:
                rgb = rgb.resize(tuple(max(1,round(n*scale)) for n in rgb.size), Image.Resampling.LANCZOS)
            out = io.BytesIO(); rgb.save(out, format='JPEG', quality=85); data = out.getvalue()
            result.append((i,i/fps,hashlib.sha256(data).hexdigest(),base64.b64encode(data).decode()))
    finally:
        if cap is not None:
            cap.release()
    return tuple(result)


def checked_result(text, labels):
    match = re.search(r'\{.*\}', text, re.S)
    if not match:
        raise ValueError('Missing JSON judge answer')
    value = json.loads(match.group()); scores, pairs = value['scores'], value['pairs']
    if len(scores)!=len(labels) or {r['id'] for r in scores}!=set(labels):
        raise ValueError('Missing or duplicate clip score')
    for row in scores:
        s=row['score']
        if s is not None and (type(s) is not int or not 1<=s<=5):
            raise ValueError('Invalid anchored score')
    expected={frozenset((a,b)) for a in labels for b in labels if a<b}
    actual=[frozenset((r['left'],r['right'])) for r in pairs]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('Missing or duplicate pair')
    if any(r['winner'] not in (r['left'],r['right'],'tie') for r in pairs):
        raise ValueError('Invalid pair winner')
    return value


def cached_result(target, benchmark, case, trial, sources):
    if not target.is_file():
        return None
    old=json.loads(target.read_text())
    fields=('version','judge','rubric_sha256','benchmark','case','trial','source_identities')
    if tuple(old.get(k) for k in fields) != (VERSION,GPT61_JUDGE,RUBRIC_SHA256,benchmark,case,trial,sources):
        raise ValueError('Existing result differs in judge, protocol, or source identity')
    return old


def next_raw_index(path):
    return max((int(p.stem) for p in path.glob('*.json') if p.stem.isdecimal()),default=-1)+1


def evaluate(client, benchmark, case, trial, videos, root):
    target=root/'results'/benchmark/f'{case}_{trial}.json'
    sources={x['model']:x['identity'] for x in videos}
    old=cached_result(target,benchmark,case,trial,sources)
    if old is not None:
        return old
    clips=sorted(videos,key=lambda r:r['model'])
    random.Random(int(hashlib.sha256(f'{VERSION}:{benchmark}:{case}'.encode()).hexdigest(),16)).shuffle(clips)
    if trial==1:clips.reverse()
    mapping={};hexes={}
    parts=[{'type':'input_text','text':RUBRIC}]
    for i,clip in enumerate(clips):
        label=chr(65+i);mapping[label]=clip['model'];hexes[label]=[]
        parts.append({'type':'input_text','text':f'Anonymous Clip {label}: sampled frames from the submitted clip.'})
        for fr,ts,h,b64 in sample_frames(clip['source'],clip['frames'],clip['fps']):
            parts.append({'type':'input_text','text':f'Clip{label}, frame{fr}, time{ts:.3f}s'})
            parts.append({'type':'input_image','image_url':'data:image/jpeg;base64,'+b64})
            hexes[label].append({'frame':fr,'sha256':h})
    request={'model':GPT61_JUDGE,'input':[{'role':'system','content':SYSTEM},{'role':'user','content':parts}],'max_output_tokens':8192}
    raw=root/'api_originals'/benchmark/f'{case}_{trial}'
    first_index=next_raw_index(raw)
    for attempt in range(5):
        try:
            reply=client.responses.create(**request)
            atomic_json(raw/f'{first_index+attempt:02d}.json', {'request':request,'response':reply.model_dump()})
            if reply.status!='completed':raise ValueError('Incomplete judge response')
            score=checked_result(reply.output_text,mapping)
            out={'version':VERSION,'judge':GPT61_JUDGE,'benchmark':benchmark,'case':case,'trial':trial,
                 'source_identities':sources,'anonymous_mapping':mapping,'input_frame_hashes':hexes,**score,
                 'response_id':reply.id,'usage':reply.usage.model_dump()if reply.usage else None,
                 'rubric_sha256':RUBRIC_SHA256,'finished_utc':utc_now()}
            atomic_json(target,out);return out
        except RateLimitError as error:
            if attempt==4:raise
            after=error.response.headers.get('Retry-After','') if error.response else ''
            time.sleep(min(90,float(after))if after.isdigit()else 30)
        except (APIConnectionError, ValueError, KeyError, TypeError):
            if attempt==4:raise
            time.sleep(2*(attempt+1))


def load_manifest(path):
    rows=json.loads(path.read_text())['videos'];jobs=defaultdict(list);identities=set()
    for r in rows:
        key=(r['benchmark'],r['case'],r['model'])
        if any(not isinstance(v,str)or not SLUG.fullmatch(v) or v in ('.','..')for v in key) or key in identities:
            raise ValueError(f'Invalid or duplicate cohort identity {key!r}')
        if type(r['frames']) is not int or r['frames']<8 or not 0<r['fps']<=240:
            raise ValueError('Missing video FPS or frame scope')
        identities.add(key);jobs[key[:2]].append(r)
    return jobs


def summarize(rows):
    scores=defaultdict(list); pairs=defaultdict(Counter)
    for r in rows:
        bench=r['benchmark'];mapping=r['anonymous_mapping']
        for item in r['scores']:
            scores[(bench,mapping[item['id']])].append((r['case'],item['score']))
        for item in r['pairs']:
            a,b=sorted(mapping[x]for x in (item['left'],item['right']))
            pairs[(bench,a,b)]['tie'if item['winner']=='tie'else mapping[item['winner']]]+=1
    return {'cohorts':[{'benchmark':b,'model':m,'cases':len({c for c,_ in a}),
                        'ratings':len(r:=[s for _,s in a if s is not None]),'no_evidence':len(a)-len(r),
                        'mean':statistics.fmean(r)if r else None}for (b,m),a in sorted(scores.items())],
            'pairs':[{'benchmark':b,'left':l,'right':r,'winner_counts':dict(c)}for (b,l,r),c in sorted(pairs.items())]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--base-url',required=True)
    p.add_argument('--workers',type=int,default=3);p.add_argument('--limit-per-benchmark',type=int)
    a=p.parse_args();key=os.environ.get('PIXELWORLD_JUDGE_API_KEY')
    if not key or a.workers<1:p.error('Expected PIXELWORLD_JUDGE_API_KEY and positive workers')
    jobs=load_manifest(a.manifest);selected=[];counter=Counter()
    for (b,c),clips in sorted(jobs.items()):
        if a.limit_per_benchmark is not None and counter[b]>=a.limit_per_benchmark:continue
        counter[b]+=1
        selected.extend((b,c,t,clips)for t in (0,1))
    if not selected:raise ValueError('No video cases')
    a.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    client=OpenAI(api_key=key,base_url=a.base_url,timeout=240,max_retries=0)
    rows=[];failures=[];status={'judge':GPT61_JUDGE,'version':VERSION,'rubric_sha256':RUBRIC_SHA256,'manifest_sha256':sha256(a.manifest),'planned_cases':dict(counter),'expected_calls':len(selected),'scores_physics':False,'sampling':'Eight frames across each complete submitted clip; 768px max long side; JPEG85; reversed second presentation'}
    with (a.output/'.photographic_judge.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        execution=a.output/'execution.json'
        if execution.is_file():
            old=json.loads(execution.read_text())
            if any(old.get(k)!=status[k]for k in ('manifest_sha256','version','judge','rubric_sha256','expected_calls')):
                raise ValueError('Existing evaluation has a different manifest, scope, rubric, or judge')
            history=a.output/'execution_history'/f'{sha256(execution)}.json'
            if not history.exists():
                atomic_json(history,old)
        todo=[]
        for b,c,t,clips in selected:
            target=a.output/'results'/b/f'{c}_{t}.json'
            old=cached_result(target,b,c,t,{x['model']:x['identity']for x in clips})
            if old is None:
                todo.append((b,c,t,clips))
            else:
                rows.append(old)
        status['reused_calls']=len(rows)
        atomic_json(execution,{**status,'phase':'running','completed_calls':len(rows),'failures':[]})
        with ThreadPoolExecutor(max_workers=a.workers)as pool:
            pending={pool.submit(evaluate,client,*job,a.output):job[:3]for job in todo}
            for f in as_completed(pending):
                try:rows.append(f.result())
                except Exception as e:
                    b,c,t=pending[f];failures.append({'benchmark':b,'case':c,'trial':t,'error':str(e)[-500:]})
                atomic_json(execution,{**status,'phase':'running','completed_calls':len(rows),'failures':failures})
        summary={**status,**summarize(rows),'phase':'finished' if not failures else 'incomplete',
                 'completed_calls':len(rows),'failures':failures,'finished_utc':utc_now()}
        atomic_json(a.output/'summary.json',summary);atomic_json(execution,summary)
    print(json.dumps(summary,indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
