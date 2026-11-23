"""Run the pinned RBench Common Manipulation GPT branch with GPT-6.1-Sol.

The upstream video sampling, prompts, JSON extraction, and score formula are
unchanged. This is a single-judge adaptation, not the published GPT+Qwen
leaderboard aggregate. Source videos and checkpoints remain outside Git.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

from openai import OpenAI

from pixelworld.util import atomic_json, sha256, utc_now
from tools.evaluate_phygen_three_stage_gpt55 import GPT61_JUDGE

UPSTREAM = 'b03df27f0376faa148dcd8cd620a1989a32ca979'
VIDEO = re.compile(r'(\d{4})\.mp4$')


def load_official(repo: Path):
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip() != UPSTREAM:
        raise ValueError('Unexpected RBench code revision')
    relative = 'eval/5_tasks/common_manipulation.py'
    script = repo/relative
    source = subprocess.check_output(['git','-C',str(repo),'show',f'{UPSTREAM}:{relative}'])
    if script.read_bytes() != source:
        raise ValueError('Pinned official scorer has local changes')
    spec = importlib.util.spec_from_file_location('rbench_official_common',script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module,script


class OfficialGPTTransport:
    """Satisfy the upstream GPT call interface without changing its VQA rubric."""

    def __init__(self, client, raw_dir: Path):
        self.client, self.raw_dir = client, raw_dir
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, model, messages, max_completion_tokens, seed):
        if model != 'gpt-5-2025-08-07' or seed != 2026:
            raise ValueError('Unexpected upstream VLM request')
        content = []
        for message in messages:
            parts = []
            for part in message['content']:
                if part['type'] == 'text':
                    parts.append({'type':'input_text','text':part['text']})
                elif part['type'] == 'image_url':
                    parts.append({'type':'input_image','image_url':part['image_url']['url']})
                else:
                    raise ValueError('Unsupported upstream content')
            content.append({'role':message['role'],'content':parts})
        request = {'model':GPT61_JUDGE,'input':content,'max_output_tokens':max_completion_tokens}
        result = self.client.responses.create(**request)
        atomic_json(self.raw_dir/f'{result.id}.json',{'request':request,'response':result.model_dump(),
                    'seed_requested_by_upstream':seed,'seed_supported_by_transport':False})
        if result.status != 'completed':
            raise ValueError(f'Judge response did not finish: {result.status}')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result.output_text))])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--official-repo',type=Path,required=True)
    p.add_argument('--videos',type=Path,required=True,help='Official filenames 0001.mp4 ... 0050.mp4')
    p.add_argument('--prompts',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--base-url',required=True)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--acceptance-only',action='store_true',help='Permit a partial set; never claim the full subset score')
    args=p.parse_args();key=os.environ.get('PIXELWORLD_JUDGE_API_KEY')
    if not key or args.workers<1:p.error('Expected PIXELWORLD_JUDGE_API_KEY and positive worker count')
    if args.output.exists():p.error('Use a fresh output directory to preserve prior scores')
    files=sorted(args.videos.iterdir());ids=[]
    for f in files:
        match=VIDEO.fullmatch(f.name)
        if not f.is_file() or not match:p.error('Use only the official numeric MP4 names')
        ids.append(int(match.group(1)))
    if not ids or min(ids)<1 or max(ids)>50 or (not args.acceptance_only and ids!=list(range(1,51))):
        p.error('Expected the exact complete Common Manipulation subset unless verifying an acceptance case')
    prompts=json.loads(args.prompts.read_text())
    if len(prompts)!=50 or any(r.get('name')!=f'common_manipulation_{i:04d}' for i,r in enumerate(prompts,1)):
        p.error('Unexpected Common Manipulation official prompts')
    official,script=load_official(args.official_repo)
    output=args.output.resolve();output.mkdir(parents=True,mode=0o700);raw=output/'api_originals';raw.mkdir(mode=0o700)
    transport=OfficialGPTTransport(OpenAI(api_key=key,base_url=args.base_url,timeout=180,max_retries=0),raw)
    official.OpenAI=lambda **_: transport
    grids=official.Video_preprocess().convert_video_to_grid(str(args.videos.resolve()),num_image=6)
    jobs=[(f'{i:04d}.jpg',prompts,grids,None) for i in ids]
    with ThreadPoolExecutor(max_workers=args.workers)as pool:
        results=list(pool.map(official.process_single_image_gpt,jobs))
    official.save_results_to_csv(results,output/'results.csv')
    failures=[];scores=[]
    for i,row in zip(ids,results,strict=True):
        try:scores.append(official.compute_final_score(row['response']))
        except (KeyError,TypeError,ValueError):failures.append(i)
    from statistics import fmean
    full_valid=not args.acceptance_only and not failures and ids==list(range(1,51))
    result={'protocol':'rbench-common-official-gpt-branch-gpt61-sol-v1','official_code_commit':UPSTREAM,
            'scorer_sha256':sha256(script),'manifest_sha256':sha256(args.prompts),'judge':GPT61_JUDGE,
            'official_combined_gpt_qwen_leaderboard_equivalent':False,'full_common50':full_valid,
            'expected_cases':len(ids),'scored_cases':len(scores),'failed_cases':failures,
            'mean':fmean(scores) if full_valid else None,'finished_utc':utc_now()}
    atomic_json(output/'summary.json',result);print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
