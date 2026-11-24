"""Archive finalized photographic judge evidence and link exact paper run versions."""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import re

from pixelworld.util import atomic_json, sha256, utc_now
from tools.archive_phygen_scores import archive_pairs
from tools.evaluate_video_photographic_gpt61 import (
    GPT61_JUDGE, RUBRIC_SHA256, VERSION, checked_result, load_manifest,
)


def exact_photo_pair(proxy, rid, evidence):
    if not re.fullmatch(r'resp_[a-zA-Z0-9]+',rid):
        raise ValueError('Invalid response ID')
    row, sdk_path = evidence
    sdk_record = json.loads(sdk_path.read_text())
    wire = {k:(proxy/f'{rid}.{k}').read_bytes() for k in ('input.json','response.json')}
    request,response = map(json.loads,wire.values())
    if (request != sdk_record['request'] or request['model'] != GPT61_JUDGE or
            response['id'] != row['response_id'] or response['status'] != 'completed'):
        raise ValueError('Proxy photo judge request or response identity mismatch')
    hashes = [hashlib.sha256(base64.b64decode(p['image_url'].split(',',1)[1],validate=True)).hexdigest()
              for m in request['input'] if m['role']=='user' for p in m['content'] if p['type']=='input_image']
    expected = [r['sha256'] for label in sorted(row['anonymous_mapping']) for r in row['input_frame_hashes'][label]]
    if hashes != expected:
        raise ValueError('Proxy photo judge image sequence mismatch')
    text = ''.join(p.get('text','') for item in response['output'] if item.get('type')=='message'
                   for p in item.get('content',[]) if p.get('type')=='output_text')
    value = checked_result(text,row['anonymous_mapping'])
    if any(value[k] != row[k] for k in ('scores','pairs')):
        raise ValueError('Proxy photo judge decision mismatch')
    if any(response['usage'][k] != row['usage'][k] for k in ('input_tokens','output_tokens','total_tokens')):
        raise ValueError('Proxy photo judge usage mismatch')
    return wire


def collect(evaluation, manifest):
    status = json.loads((evaluation/'execution.json').read_text())
    if (status['phase'], status['manifest_sha256'], status['rubric_sha256'], status['judge']) != (
            'finished',sha256(manifest),RUBRIC_SHA256,GPT61_JUDGE) or status['failures']:
        raise ValueError('Archive only finalized results for the exact manifest and rubric')
    jobs = load_manifest(manifest)
    if status['completed_calls'] != 2*len(jobs) or status['expected_calls'] != 2*len(jobs):
        raise ValueError('Photographic result coverage does not match the input manifest')
    calls, scores = {}, []
    for (benchmark,case), videos in sorted(jobs.items()):
        for trial in (0,1):
            path=evaluation/'results'/benchmark/f'{case}_{trial}.json'
            row=json.loads(path.read_text())
            if (row['version'],row['judge'],row['benchmark'],row['case'],row['trial'],row['rubric_sha256']) != (
                VERSION,GPT61_JUDGE,benchmark,case,trial,RUBRIC_SHA256
            ) or row['source_identities'] != {v['model']:v['identity']for v in videos}:
                raise ValueError('Photographic score source identity mismatch')
            rid=row['response_id']
            originals=(evaluation/'api_originals'/benchmark/f'{case}_{trial}').glob('*.json')
            matches=[p for p in originals if json.loads(p.read_text())['response']['id']==rid]
            if len(matches)!=1 or rid in calls:
                raise ValueError('Cannot bind each finalized score to a unique saved raw reply')
            calls[rid]=(row,matches[0])
            scores.append((path,row,videos))
    return status,calls,scores


def archive(evaluation,manifest,paper,proxy,output):
    os.umask(0o077)
    status,calls,scores=collect(evaluation,manifest)
    with (paper/'runs.csv').open() as f:
        identities={(r['benchmark'],r['model'],r['case']):r['directory']for r in csv.DictReader(f)}
    output.mkdir(mode=0o700,exist_ok=False)
    index=archive_pairs(proxy,output/'wire',calls,verify=exact_photo_pair)
    links=defaultdict(list)
    unlinked=set()
    for src,row,videos in scores:
        destination=output/'scores'/row['benchmark']/src.name
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        data=src.read_bytes();destination.write_bytes(data)
        if sha256(destination)!=hashlib.sha256(data).hexdigest():
            raise ValueError('Photographic score readback mismatch')
        for v in videos:
            k=(v['benchmark'],v['model'],v['case'])
            rel=identities.get(k)
            if not rel:
                unlinked.add(k);continue
            paper_run=(paper/rel).resolve()
            if not paper_run.is_relative_to(paper.resolve()):
                raise ValueError('Paper archive entry points outside the approved root')
            record=json.loads((paper_run/'run.json').read_text())
            if record['run_manifest_sha256']!=v['identity'] or record['selected_run_original_location']!=v['source']:
                unlinked.add(k);continue
            links[rel].append({'trial':row['trial'],'case':row['case'],'score_sha256':sha256(destination),
                       'score_file':str(destination.relative_to(output)),'response_id':row['response_id']})
    for run,rows in links.items():
        if {r['trial']for r in rows}!={0,1} or len(rows)!=2:
            raise ValueError('Paired photo order coverage missing for an accepted generation run')
        atomic_json(output/'by_run'/run/'photo_score_links.json',{'paper_run':run,'trials':rows,'wire_index':'wire/index.json'})
    atomic_json(output/'unlinked_sources.json',sorted(unlinked))
    result={'status':'verified','score_calls':len(scores),'exact_wire_pairs':len(index),'linked_paper_runs':len(links),
            'unlinked_source_clips':len(unlinked),'manifest_sha256':sha256(manifest),
            'judge':status['judge'],'rubric_sha256':RUBRIC_SHA256,'completed_utc':utc_now()}
    atomic_json(output/'verified.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('evaluation','manifest','paper','proxy','output'):
        p.add_argument('--'+arg,type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(archive(a.evaluation,a.manifest,a.paper,a.proxy,a.output)))


if __name__=='__main__':
    main()
