"""Attach RBench judge wire originals only after matching the official prompt, grid, and decision."""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from pixelworld.util import atomic_json, sha256
from tools.archive_rbench_scores import PROTOCOL, attach, index_receipts
from tools.evaluate_rbench_common_gpt61 import GPT61_JUDGE, load_official
from tools.watch_rbench_common_scores import read_upstream_details


def key(request):
    return hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def decision(response, official):
    text = ''.join(c.get('text','')for item in response.get('output',[]) if item.get('type')=='message'
                   for c in item.get('content',[])if c.get('type')=='output_text')
    return official.extract_json(text)


def resolve_call(raw_records, request, expected, official):
    matches = []
    for path in raw_records.get(key(request), []):
        candidate = json.loads(path.read_text())
        response = candidate['response']
        if candidate['request'] != request or response.get('status') != 'completed':
            continue
        try:
            if decision(response,official) == expected:
                matches.append(response)
        except ValueError:
            continue
    return matches[0] if len(matches)==1 else None


def readback(path, body):
    expected = hashlib.sha256(body).hexdigest()
    if path.is_file():
        if sha256(path) != expected:
            raise ValueError('Existing judge archive represents different bytes')
    else:
        tmp = path.with_suffix('.part')
        with tmp.open('xb')as f:
            f.write(body)
        try:
            if sha256(tmp) != expected:
                raise ValueError('Judge wire readback mismatch')
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    return expected


def attach_wire(score_path, pilot, raw_index, proxy, lookup, official, prompts):
    row = json.loads(score_path.read_text())
    model, case = row['model'],row['case']
    identity={'model':model,'case':case}
    if row['protocol'] != PROTOCOL or row['judge'] != GPT61_JUDGE or not re.fullmatch(r'[0-9]{4}',case):
        raise ValueError('Unexpected score row')
    is_pilot='reused_pilot_metrics' in row
    root=pilot if is_pilot else score_path.parent.parent
    video=root/'videos'/f'{case}.mp4'
    grid=root/'image_grid_6frame'/f'{case}.jpg'
    if not video.is_file() or not grid.is_file() or sha256(video)!=row['video_sha256']:
        return {**identity,'status':'video_or_grid_unverified'}
    if is_pilot:
        csv_path=pilot/'evaluation/results.csv'
        if Path(row['reused_pilot_metrics']).resolve()!=csv_path.resolve():
            raise ValueError('Pilot score changed source')
        with csv_path.open() as f: rows=list(csv.DictReader(f))
        if len(rows)!=1 or rows[0]['name']!=f'common_manipulation_{case}':
            raise ValueError('Pilot score has wrong case')
        expected=read_upstream_details(rows[0]['details'])
    else:
        expected=row['response']
    info=prompts[int(case)-1]
    prompt=official.create_prompt(info['view'],info['prompt'],info['robotic manipulator'],info['manipulated object'])
    request={'model':GPT61_JUDGE,'input':[{'role':'user','content':[{'type':'input_text','text':prompt},
            {'type':'input_image','image_url':'data:image/jpeg;base64,'+official.encode_image(grid)}]}],
            'max_output_tokens':8000}
    match=resolve_call(raw_index,request,expected,official)
    if match is None:
        return {**identity,'status':'no_unique_exact_judge_call'}
    rid=match['id']
    if not re.fullmatch(r'resp_[a-zA-Z0-9]+',rid):
        raise ValueError('Invalid judge API response ID')
    incoming,outgoing=((proxy/f'{rid}.{role}.json').read_bytes() for role in ('input','response'))
    response=json.loads(outgoing)
    if json.loads(incoming)!=request or response['id']!=rid or decision(response,official)!=expected or response['usage']!=match['usage']:
        raise ValueError('Proxy original differs from matched judge evidence')
    binding=attach(score_path,lookup)
    if binding['status']!='verified':
        return {**identity,'status':binding['status']}
    records=lookup[(row['source_run'],row['source_manifest_sha256'])]
    archive=records[0][0].parent/'evaluations'/PROTOCOL/'judge_wire'
    if archive.resolve()!=archive:
        raise ValueError('Unexpected judge archive path')
    archive.mkdir(mode=0o700,exist_ok=True)
    with (archive/'.attach_lock').open('a')as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        fields={'response_id':rid,'score_sha256':sha256(score_path),'source_manifest_sha256':row['source_manifest_sha256'],
                'grid_sha256':sha256(grid),'input_sha256':readback(archive/'input.json',incoming),
                'response_sha256':readback(archive/'response.json',outgoing),'judge':GPT61_JUDGE}
        atomic_json(archive/'verified.json',fields)
    return {**identity,'status':'verified','response_id':rid}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('scores','pilot','proxy','archive','official-repo','prompts','report'):
        p.add_argument('--'+arg,type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    official,_=load_official(a.official_repo)
    prompts=json.loads(a.prompts.read_text())
    if [row['name']for row in prompts]!=[f'common_manipulation_{i:04d}'for i in range(1,51)]:
        raise ValueError('Unexpected official prompt set')
    paths=list(a.scores.glob('*/api_originals/resp_*.json'))+list((a.pilot/'evaluation/api_originals').glob('resp_*.json'))
    raw_index={}
    for path in paths:
        row=json.loads(path.read_text());raw_index.setdefault(key(row['request']),[]).append(path)
    lookup=index_receipts(a.archive.resolve())
    report=[]
    for score in sorted(a.scores.glob('*/results/[0-9][0-9][0-9][0-9].json')):
        try:
            report.append(attach_wire(score,a.pilot,raw_index,a.proxy,lookup,official,prompts))
        except (OSError,ValueError,KeyError,TypeError)as error:
            report.append({'model':score.parent.parent.name,'case':score.stem,'status':'unverified','error_type':type(error).__name__})
    summary={'verified':sum(row['status']=='verified'for row in report),'seen':len(report),'rows':report}
    atomic_json(a.report,summary)
    print(json.dumps({k:v for k,v in summary.items()if k!='rows'}))


if __name__=='__main__':
    main()
