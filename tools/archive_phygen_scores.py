"""Preserve final PhyGen GPT-6.1 judge scores and their exact proxy wire pairs privately."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile

from pixelworld.util import atomic_json, sha256, utc_now
from tools.evaluate_phygen_three_stage_gpt55 import GPT61_JUDGE, GPT61_VERSION

SUITE = 'phygenbench_t2v_main720p24fps'


def read(path):
    return json.loads(path.read_text())


def exact_pair(root, rid, call):
    if not re.fullmatch(r'resp_[a-zA-Z0-9]+', rid):
        raise ValueError('Invalid response identity')
    wire = {suffix: (root/f'{rid}.{suffix}').read_bytes() for suffix in ('input.json','response.json')}
    request, response = map(json.loads, wire.values())
    if request['model'] != GPT61_JUDGE or response['id'] != rid or response['status'] != 'completed':
        raise ValueError('Wire identity differs from the accepted judge call')
    user = [p for m in request['input'] if m.get('role') == 'user' for p in m['content']]
    images = [p['image_url'] for p in user if p['type'] == 'input_image']
    expected = [call['frame_image_sha256'][str(i)] for i in call['frames']]
    actual = [hashlib.sha256(base64.b64decode(v.split(',',1)[1],validate=True)).hexdigest() for v in images]
    if actual != expected or not any(p.get('text')==call['prompt'] for p in user if p['type']=='input_text'):
        raise ValueError('Recorded judge prompt or image bytes mismatch')
    raw = ''.join(p.get('text','') for item in response['output'] if item.get('type')=='message'
                  for p in item['content'] if p.get('type')=='output_text')
    match = re.search(r'\{.*\}',raw,re.S)
    if match is None or json.loads(match.group()) != call['response']:
        raise ValueError('Recorded judge decision differs from the raw response')
    if any(response['usage'][k] != call['usage'][k] for k in ('input_tokens','output_tokens','total_tokens')):
        raise ValueError('Recorded judge usage differs from provider report')
    return wire


def archive_pairs(proxy, output, calls):
    output.mkdir(mode=0o700)
    index = {}
    ids = sorted(calls)
    for start in range(0,len(ids),100):
        batch = ids[start:start+100]
        target = output/f'wire_{start//100+1:04d}.tar.gz'
        part = target.with_suffix('.part')
        expected = {}
        with tarfile.open(part,'w:gz',compresslevel=1) as archive:
            for rid in batch:
                for suffix,body in exact_pair(proxy,rid,calls[rid]).items():
                    member = tarfile.TarInfo(f'{rid}/{suffix}')
                    member.size = len(body)
                    member.mode = 0o600
                    archive.addfile(member,io.BytesIO(body))
                    expected[member.name] = hashlib.sha256(body).hexdigest()
        with tarfile.open(part,'r|gz') as archive:
            visited = {}
            for member in archive:
                if member.name in visited or not member.isfile():
                    raise ValueError('Invalid archive readback member')
                with archive.extractfile(member) as stream:
                    visited[member.name] = hashlib.file_digest(stream,'sha256').hexdigest()
        if visited != expected:
            raise ValueError('Archive readback does not match the original proxy bodies')
        part.replace(target)
        for rid in batch:
            index[rid] = {'shard':target.name,'input_sha256':expected[f'{rid}/input.json'],
                          'response_sha256':expected[f'{rid}/response.json']}
        atomic_json(output/'progress.json',{'verified_pairs':len(index),'expected_pairs':len(ids)})
    atomic_json(output/'index.json',index)
    return index


def archive_scores(score_roots,paper,proxy,output):
    os.umask(0o077)
    rows, calls = {}, {}
    for root in score_roots:
        summary = read(root/'summary.json')
        execution = read(root/'execution.json')
        if (summary.get('judge'),summary.get('version'),execution['phase']) != (GPT61_JUDGE,GPT61_VERSION,'finished'):
            raise ValueError('Use only finalized GPT-6.1 PhyGen results')
        for path in sorted(root.glob('*/[0-9][0-9][0-9].json')):
            row = read(path)
            key = (row['model'],row['case'])
            if (key in rows or not re.fullmatch(r'[a-z0-9-]+',row['model']) or not 1<=row['case']<=160 or
                    len(row['stage_scores'])!=3 or (row['judge'],row['version'])!=(GPT61_JUDGE,GPT61_VERSION)):
                raise ValueError('Unexpected or repeated score identity')
            rows[key]=(path,row)
            for call in row['calls']:
                rid=call['response_id']
                if calls.setdefault(rid,call) != call:
                    raise ValueError('Conflicting records share one response ID')
    if len(rows) != sum(read(r/'execution.json')['completed_jobs'] for r in score_roots):
        raise ValueError('Score file count differs from final evaluation receipts')
    with (paper/'runs.csv').open() as f:
        identities={(row['model'],row['case']):paper/row['directory'] for row in csv.DictReader(f) if row['benchmark']==SUITE}
    output.mkdir(mode=0o700,exist_ok=False)
    wire = archive_pairs(proxy,output/'wire',calls)
    linked = 0
    for (model,case),(source,row) in sorted(rows.items()):
        case_id=f'phygen_{case:03d}'
        paper_run=identities.get((model,case_id))
        binding={'status':'unlinked_video_source','recorded_source':row['source']}
        local=f'case_{case:03d}'
        if paper_run is not None:
            prior=read(paper_run/'run.json')
            if prior['selected_run_original_location']==row['source']:
                location=Path(row['source'])/'run_manifest.json'
                status='matched_source_path_and_manifest' if location.is_file() and sha256(location)==prior['run_manifest_sha256'] else 'matched_source_path_only'
                binding={'status':status,'paper_run':str(paper_run.relative_to(paper)),
                         'paper_manifest_sha256':prior['run_manifest_sha256'],'recorded_source':row['source']}
                local=paper_run.name
                linked+=1
        destination=output/SUITE/model/local
        destination.mkdir(mode=0o700,parents=True)
        original=source.read_bytes()
        (destination/'score.json').write_bytes(original)
        digest=hashlib.sha256(original).hexdigest()
        if sha256(destination/'score.json')!=digest:
            raise ValueError('Score readback mismatch')
        ids=[call['response_id']for call in row['calls']]
        atomic_json(destination/'verified.json',{'model':model,'case':case_id,'score_sha256':digest,'source_binding':binding,
                    'judge':GPT61_JUDGE,'score_protocol':GPT61_VERSION,'wire_index':'../../../wire/index.json',
                    'verified_wire_ids':[rid for rid in ids if rid in wire]})
    report={'status':'verified','scores':len(rows),'exact_wire_pairs':len(wire),'linked_paper_runs':linked,
            'unlinked_video_sources':len(rows)-linked,'finished_utc':utc_now()}
    atomic_json(output/'verified.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scores',type=Path,nargs='+',required=True)
    for name in ('paper','proxy','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(archive_scores(a.scores,a.paper,a.proxy,a.output)))


if __name__=='__main__':
    main()
