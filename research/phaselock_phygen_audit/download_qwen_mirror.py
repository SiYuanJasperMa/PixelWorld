"""Fetch the identical pinned weights from the official ModelScope mirror."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import requests

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'runs/phygen_i2v_qwen21_20261111'
BLOCK=128*1024*1024
LOCK=threading.Lock()
DONE=0

def report(phase,**extra):
    payload={'phase':phase,'pid':os.getpid(),'updated_at':time.time(),**extra}
    temp=RUN/'logs/model_mirror_status.tmp'
    temp.write_text(json.dumps(payload,indent=2));temp.replace(RUN/'logs/model_mirror_status.json')

def part_download(item):
    global DONE
    row,start,end,part=item
    wanted=end-start+1
    if part.is_file() and part.stat().st_size==wanted:return part
    for attempt in range(3):
        try:
            url='https://modelscope.cn/models/Qwen/Qwen-Image-2.1/resolve/master/'+row['rfilename']
            with requests.get(url,headers={'Range':f'bytes={start}-{end}'},stream=True,timeout=(20,90)) as response:
                response.raise_for_status()
                if response.status_code!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {start}-{end}/'):
                    raise ValueError('Mirror did not return the requested byte range')
                temporary=part.with_suffix('.tmp')
                with temporary.open('wb') as handle:
                    for chunk in response.iter_content(1024*1024):handle.write(chunk)
                if temporary.stat().st_size!=wanted:raise ValueError('Incomplete mirror part')
                temporary.replace(part)
            with LOCK:
                DONE+=wanted;report('downloading',new_bytes=DONE,latest=row['rfilename'],range_start=start)
            return part
        except Exception:
            if attempt==2:raise
            time.sleep(2*(attempt+1))

def main():
    config=json.loads((RUN/'protocol.json').read_text())
    hf=json.loads((RUN/'runtime/hf_pinned_files.json').read_text())
    mirror=json.loads((RUN/'runtime/modelscope_files.json').read_text())
    mirror_rows={x['Path']:x for x in mirror['Data']['Files'] if x.get('Type')=='blob'}
    weights=[x for x in hf['siblings'] if x['rfilename'].endswith('.safetensors')]
    for row in weights:
        m=mirror_rows[row['rfilename']]
        assert row['lfs']['sha256']==m['Sha256'] and row['size']==m['Size'], 'Mirror checkpoint differs from pinned HF weights'
    cache=RUN/'runtime/model_cache/models--Qwen--Qwen-Image-2.1'
    blob=cache/'blobs';snapshot=cache/'snapshots'/config['initializer_revision']
    blob.mkdir(parents=True,exist_ok=True);snapshot.mkdir(parents=True,exist_ok=True)
    jobs=[];file_parts={}
    for row in weights:
        sha=row['lfs']['sha256'];target=blob/sha
        if target.is_file() and target.stat().st_size==row['size']:continue
        folder=RUN/'runtime/model_mirror_parts'/sha;folder.mkdir(parents=True,exist_ok=True)
        parts=[]
        for start in range(0,row['size'],BLOCK):
            end=min(row['size']-1,start+BLOCK-1);part=folder/f'{start:012d}.part'
            parts.append(part);jobs.append((row,start,end,part))
        file_parts[row['rfilename']]=(row,parts)
    report('downloading',parts=len(jobs),total_weight_bytes=sum(x['size'] for x in weights),same_checkpoint_sha256_verified=True)
    with ThreadPoolExecutor(max_workers=24) as pool:
        for future in as_completed([pool.submit(part_download,j) for j in jobs]):future.result()
    report('assembling_and_verifying')
    for row in weights:
        sha=row['lfs']['sha256'];target=blob/sha
        if row['rfilename'] in file_parts:
            temporary=target.with_suffix('.mirror.tmp');digest=hashlib.sha256()
            with temporary.open('wb') as handle:
                for part in file_parts[row['rfilename']][1]:
                    with part.open('rb') as source:
                        while chunk:=source.read(4*1024*1024):handle.write(chunk);digest.update(chunk)
            if digest.hexdigest()!=sha:raise ValueError('Downloaded weight SHA256 mismatch')
            temporary.replace(target)
        destination=snapshot/row['rfilename'];destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():
            destination.unlink(missing_ok=True);destination.symlink_to(os.path.relpath(target,destination.parent))
    for row in hf['siblings']:
        name=row['rfilename']
        if name.startswith('assets/') or name.endswith(('.png','.jpg','.md','.safetensors')):continue
        destination=snapshot/name
        if destination.is_file():continue
        response=requests.get(f"https://huggingface.co/Qwen/Qwen-Image-2.1/resolve/{config['initializer_revision']}/{name}",timeout=60);response.raise_for_status()
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(response.content)
    (RUN/'runtime/checkpoint_snapshot.txt').write_text(str(snapshot)+'\n')
    report('complete',snapshot=str(snapshot),weights_sha256_verified=True)
    (RUN/'logs/setup_status.json').write_text(json.dumps({'phase':'ready','updated_at':time.time(),'snapshot':str(snapshot),'model':config['initializer'],'revision':config['initializer_revision'],'mirror_weights_same_sha256':True},indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        with LOCK:report('failed',error=f'{type(error).__name__}: {error}')
        raise
