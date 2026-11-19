"""Coordinate a frozen A/C/D initialization analysis and publish its evidence.

Image review uses only caption/pre-action suitability, before video generation.
Invalid initialization is retained and flagged; no downstream-score tuning.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from tools.prepare_phygen_acd_smoke import write

REMOTE = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/smoke/phygen_acd10/'


def read(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return {}


def init_review(client, row, path):
    from PIL import Image
    # No video, physical-law hints, previous scores or other candidates are given.
    with Image.open(path) as image:
        image = image.convert('RGB'); image.thumbnail((768, 768))
        buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=85)
    prompt = ('Check this single initialization image for a later video event: ' + row['caption'] +
              '\nDoes it show the necessary scene/objects BEFORE the event starts, rather than '
              'the event already happening or its aftermath? Do not judge future video quality. '
              'Return JSON only: {"acceptable": true or false, "reason": "short factual reason"}.')
    response = client.chat.completions.create(model='gpt-5.5-2026-04-24',
        messages=[{'role': 'user', 'content': [{'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}}]}],
        max_completion_tokens=4096)
    import re
    obj = json.loads(re.search(r'\{.*\}', response.choices[0].message.content, re.S).group())
    if type(obj.get('acceptable')) is not bool: raise ValueError('Invalid initialization review')
    return {'acceptable': obj['acceptable'], 'reason': str(obj.get('reason', ''))[:300],
            'judge_model': 'gpt-5.5-2026-04-24', 'review_scope': 'caption and pre-action suitability only',
            'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'usage': response.usage.model_dump() if response.usage else None}


def ensure_configs(release, root):
    base = read(release / 'configs/phygen_acd_pixelinit_smoke.json')
    for route in ['C', 'D']:
        inputs = root / ('inputs_' + route.lower()); inputs.mkdir(parents=True, exist_ok=True)
        inventory = root / (inputs.name + '_manifest.json')
        if not inventory.exists(): write(inventory, {'cases': []})
        config = release / 'configs' / f'phygen_acd_{route.lower()}_smoke.json'
        if not config.exists():
            value = copy.deepcopy(base)
            value.update(protocol=f'pixelworld-phygen-acd-{route.lower()}-i2v-smoke10-v1',
                         inputs_root=str(inputs), runs_root=str(release / 'analysis_generations' / f'phygen_acd_{route.lower()}'),
                         case_ids=[], max_concurrency=3, max_per_config=3)
            write(config, value)


def register(release, route):
    name = f'phygen_acd_{route.lower()}_smoke'
    cfg = release / 'configs' / (name + '.json')
    path = release / 'state/recovered_worker_services.json'
    spec = read(path)
    if any(x['name'] == name for x in spec['services']): return
    base = next(x for x in spec['services'] if x['name'] == 'phygen_acd_pixelinit_smoke')
    service = copy.deepcopy(base)
    service.update(name=name, pid_file=str(release/'state'/(name+'.pid')), log=str(release/'state'/(name+'.log')),
                   identity=[str(cfg)], stop_marker=str(release/'analysis_generations'/f'phygen_acd_{route.lower()}'/'STOP'))
    command = service['command']
    command[command.index('--config')+1] = str(cfg)
    command[command.index('--state')+1] = str(release/'state'/(name+'.json'))
    spec['services'].append(service)
    write(path, spec)


def select_initialization(client, release, root, manifest, route, row):
    output = root / 'qwen' / route / row['id']
    frozen = output / 'selection.json'
    if frozen.exists(): return read(frozen)
    seeds = manifest['qwen']['seeds']
    if not all((output/f'seed{s}.render.json').is_file() for s in seeds): return None
    reviews = []
    for seed in seeds:
        review_path = output/f'seed{seed}.review.json'
        review = read(review_path)
        if not review:
            review = init_review(client, row, output/f'seed{seed}.png')
            write(review_path, review)
        reviews.append({'seed': seed, **review})
    good = next((r for r in reviews if r['acceptable']), None)
    chosen = good['seed'] if good else seeds[0]
    record = {'seed': chosen, 'acceptable': good is not None, 'reviews': reviews,
              'rule': 'first acceptable ascending seed; otherwise seed0 retained and flagged unsuitable',
              'selected_sha256': read(output/f'seed{chosen}.render.json')['output_sha256'],
              'review_before_video_generation': True}
    write(frozen, record)
    return record


def prepare_input(release, root, route, row, selection):
    cid = row['id']; dest = root / ('inputs_'+route.lower()) / cid
    if not (dest/'task_spec.json').exists():
        dest.mkdir(parents=True, exist_ok=True)
        source = root/'qwen'/route/cid/f"seed{selection['seed']}.png"
        (dest/'first_frame.png').write_bytes(source.read_bytes())
        (dest/'prompt.txt').write_text(row['caption']+'\n')
        write(dest/'preparation_record.json', {'route':route, 'official_index':row['official_index'],
              'initialization_sha256':selection['selected_sha256'], 'selection':selection})
        write(dest/'task_spec.json', {'task_type':'i2v','width':1280,'height':720,'fps':24.0,'frames':120,
              'audio':None,'benchmark':'phygenbench_i2v_'+route.lower(),'visual_target':'photoreal'})
    elif hashlib.sha256((dest/'first_frame.png').read_bytes()).hexdigest()!=selection['selected_sha256']:
        raise ValueError('Frozen initialization changed')
    cfg_path=release/'configs'/f'phygen_acd_{route.lower()}_smoke.json'
    cfg=read(cfg_path)
    if cid not in cfg['case_ids']:
        cfg['case_ids'].append(cid); cfg['case_ids'].sort(); write(cfg_path,cfg)
        write(root/f'inputs_{route.lower()}_manifest.json', {'cases':[{'id':c} for c in cfg['case_ids']]})
    register(release,route)


def exported_run(run, dest, ffmpeg):
    from tools.prepare_hf_delivery import export_trace_run
    run=Path(run); dest.mkdir(parents=True,exist_ok=True)
    preview=dest/'video.mp4'
    if not preview.exists():
        frames=sorted((run/'workspace/frames').glob('*.png'))
        if len(frames)!=120:raise ValueError('Smoke requires 120 accepted RGB frames')
        subprocess.run([ffmpeg,'-y','-loglevel','error','-framerate','24','-pattern_type','glob','-i',str(run/'workspace/frames/*.png'),
                        '-c:v','libx264','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(preview)],check=True,timeout=300)
    if not (dest/'trace.tar.gz').exists(): export_trace_run(run,dest/'trace.tar.gz')
    (dest/'generate.py').write_bytes((run/'workspace/generate.py').read_bytes())
    return preview


def publish(root, public, summary, token_file):
    from huggingface_hub import HfApi,CommitOperationAdd
    from tools.prepare_hf_delivery import sanitize
    from tools.hf_commit_budget import commit
    api=HfApi(token=token_file.read_text().strip())
    state=read(root/'publication.json'); done=state.get('assets',{})
    operations=[]; now=dict(done);size=0
    # The first progress update is tiny and must not wait for ten video/trace uploads.
    buckets={suffix:[] for suffix in ('.mp4','.png','.json','.gz','.py')}
    for path in sorted(p for p in public.rglob('*')if p.is_file()):
        name=path.relative_to(public).as_posix()
        if done.get(name)==hashlib.sha256(path.read_bytes()).hexdigest():continue
        buckets.setdefault(path.suffix,[]).append(path)
    buckets['.mp4'].sort(key=lambda p:(p.relative_to(public).parts[0]=='A',str(p)))
    buckets['.gz'].sort(key=lambda p:(p.relative_to(public).parts[0]=='A',str(p)))
    order=['.mp4','.png','.json','.gz','.py']
    if state.get('media_round',0)%3==2:order=['.gz','.mp4','.png','.json','.py']
    pending=[]
    # Serve new videos, initialization and full traces fairly. A large trace
    # receives a leading slot periodically; it cannot be displaced forever.
    while any(buckets.values()):
        for suffix in order+sorted(set(buckets)-set(order)):
            if buckets.get(suffix):pending.append(buckets[suffix].pop(0))
    if state.get('summary_digest'):
        for path in pending:
            name=path.relative_to(public).as_posix();digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if done.get(name)==digest:continue
            amount=path.stat().st_size
            if len(operations)>=6:break
            if operations and size+amount>8*1024*1024:continue
            operations.append(CommitOperationAdd(path_in_repo='live/smoke/phygen_acd10/'+name,path_or_fileobj=str(path)))
            now[name]=digest;size+=amount
    for row in summary['cases']:
        for item in row['routes'].values():
            for key in ['video','trace','init_image','pixel_init','selection']:
                url=item.get(key)
                if url and url.removeprefix(REMOTE) not in now:
                    item.pop(key)
                    if key=='video':item['media_sync_pending']=True
            if 'candidate_images' in item:
                item['candidate_images']=[url for url in item['candidate_images'] if url.removeprefix(REMOTE) in now]
    data=json.dumps(sanitize(summary),ensure_ascii=False).encode()
    # Poll time alone is not a change to a video, score or initialization.
    fingerprint=copy.deepcopy(summary);fingerprint.pop('updated_unix',None)
    digest=hashlib.sha256(json.dumps(sanitize(fingerprint),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if state.get('summary_digest')!=digest:
        operations.append(CommitOperationAdd(path_in_repo='live/smoke/phygen_acd10/progress.json',path_or_fileobj=data))
    if operations:
        result=commit(api,repo_id='HuggingFriends/PixelWorld',repo_type='dataset',operations=operations,
                      commit_message='Refresh paired A C D smoke initialization videos and traces')
        write(root/'publication.json',{'assets':now,'summary_digest':digest,'commit':result.oid,'updated_unix':time.time(),
                                      'media_round':state.get('media_round',0)+(size>0)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    args=parser.parse_args();release=args.release.resolve();root=release/'analysis/phygen_acd_smoke10'
    public=root/'public';public.mkdir(exist_ok=True)
    ensure_configs(release,root)
    from openai import AzureOpenAI
    from pixelworld.config import load_config
    from pixelworld.supervisor import inspect
    from tools.remote_case_status import remote_case_status
    from tools.evaluate_phygen_gpt55 import score_case
    manifest=read(root/'manifest.json')
    client=AzureOpenAI(api_key=(release/'state/gpt55.key').read_text().strip(),api_version='2023-07-01-preview',
              azure_endpoint='https://search.bytedance.net/gpt/openapi/online/multimodal/crawl',timeout=180)
    ffmpeg=str(release.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg')
    pool=ThreadPoolExecutor(max_workers=5);futures={};init_futures={};upload=None
    while not (root/'COORDINATOR_STOP').exists():
        errors=[]
        for key,f in list(init_futures.items()):
            if f.done():
                try:f.result()
                except Exception as exc:errors.append({'stage':'initialization_review','route':key[0],'case':key[1],'error':str(exc)[:500]})
                del init_futures[key]
        for key,f in list(futures.items()):
            if f.done():
                try:write(root/'evaluation'/key[0]/(key[1]+'.json'),f.result())
                except Exception as exc:errors.append({'stage':'video_judge','route':key[0],'case':key[1],'error':str(exc)[:500]})
                del futures[key]
        if upload is not None and upload.done():
            try:upload.result()
            except Exception as exc:errors.append({'stage':'hf_publication','error':str(exc)[:500]})
            upload=None
        summary={'protocol':manifest['protocol'],'model':manifest['model'],'track':'Vision','width':1280,'height':720,
                 'fps':24,'frames':120,'updated_unix':time.time(),'cases':[],'errors':errors,
                 'selection_rule':'first suitable initial frame; no video-score selection; unsuitable seed0 retained and flagged'}
        for row in manifest['cases']:
            entry={'id':row['id'],'caption':row['caption'],'dimension':row['dimension'],'routes':{}}
            for route in ['A','C','D']:
                try:
                    if route!='A':
                        qwen_dir=root/'qwen'/route/row['id']
                        selection=read(qwen_dir/'selection.json') or None
                        if (selection is None and (route,row['id']) not in init_futures
                            and all((qwen_dir/f'seed{s}.render.json').exists() for s in manifest['qwen']['seeds'])):
                            init_futures[(route,row['id'])]=pool.submit(select_initialization,client,release,root,manifest,route,row)
                        if selection:prepare_input(release,root,route,row,selection)
                    else:selection=None
                    config=load_config(release/'configs'/('phygenbench_full.json' if route=='A' else f'phygen_acd_{route.lower()}_smoke.json'))
                    if route!='A' and row['id'] not in (config.case_ids or []):
                        dest=public/route/row['id'];dest.mkdir(parents=True,exist_ok=True)
                        candidates=[]
                        for seed in manifest['qwen']['seeds']:
                            path=qwen_dir/f'seed{seed}.png'
                            if path.exists() and path.with_suffix('.render.json').exists():
                                (dest/path.name).write_bytes(path.read_bytes());candidates.append(REMOTE+f'{route}/{row["id"]}/{path.name}')
                        entry['routes'][route]={'state':'reviewing_initial_frame' if (route,row['id']) in init_futures else 'preparing_initial_frame',
                                              'candidate_images':candidates};continue
                    status=remote_case_status(config,config.model('gpt61-sol'),'vision-agent',row['id'],
                                             inspect(config,config.model('gpt61-sol'),'vision-agent',row['id']))
                    item={'state':status['state'],'attempt':status.get('attempt'),'initialization_suitable':selection.get('acceptable') if selection else None}
                    dest=public/route/row['id'];dest.mkdir(parents=True,exist_ok=True)
                    if selection:
                        for file in ['selection.json',f"seed{selection['seed']}.png",'agent_pixel_init.png']:
                            origin=root/'qwen'/route/row['id']/file
                            if origin.exists():(dest/file).write_bytes(origin.read_bytes())
                        item['init_image']=REMOTE+f"{route}/{row['id']}/seed{selection['seed']}.png"
                        item['selection']=REMOTE+f"{route}/{row['id']}/selection.json"
                        if (dest/'agent_pixel_init.png').exists():item['pixel_init']=REMOTE+f"{route}/{row['id']}/agent_pixel_init.png"
                    if status['state']=='complete':
                        exported_run(status['run'],dest,ffmpeg)
                        item['video']=REMOTE+f"{route}/{row['id']}/video.mp4";item['trace']=REMOTE+f"{route}/{row['id']}/trace.tar.gz"
                        record=read(Path(status['run'])/'run_manifest.json');usage=record.get('token_usage') or record.get('usage') or {}
                        item.update(output_tokens=usage.get('output_tokens'),input_tokens=usage.get('input_tokens'),elapsed_seconds=record.get('duration_seconds'),reused=route=='A')
                        score_path=root/'evaluation'/route/(row['id']+'.json');score=read(score_path)
                        if score:item['score_1_5']=score['score'];item['judge_reason']=score.get('reason')
                        elif (route,row['id']) not in futures:
                            job={'model':'gpt61-sol','case':row['id'],'run':status['run'],'attempt':status.get('attempt'),
                                 'caption':row['caption'],'physical_law':row['physical_laws'],'main_category':row['main_category'],
                                 'sub_category':row['sub_category'],'track':'vision-agent'}
                            futures[(route,row['id'])]=pool.submit(score_case,client,release/'state/gpt55.key',job,root/'evaluation'/route,8,768)
                    entry['routes'][route]=item
                except Exception as exc:
                    entry['routes'][route]={'state':'infra_stale','error':str(exc)[:500]}
            summary['cases'].append(entry)
        write(root/'progress.json',summary)
        if upload is None:upload=pool.submit(publish,root,public,copy.deepcopy(summary),args.token_file)
        time.sleep(30)


if __name__=='__main__': main()
