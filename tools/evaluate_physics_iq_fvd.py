"""Measure I3D FVD against the official Physics-IQ take-1 24 FPS references."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from pixelworld.physics_iq import official_cases
from pixelworld.util import atomic_json, sha256, utc_now

VERSION = 'physics-iq-verified-take1-i3d-fvd-v1'
BENCHMARK = 'physics_iq_720p24fps'


def frechet(a, b):
    """Equivalent to covariance FVD, using the smaller cross-feature Gram matrix."""
    if a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[1] or min(len(a),len(b)) < 2:
        raise ValueError('Two compatible feature matrices with at least two samples are required')
    a, b = a.astype(np.float64), b.astype(np.float64)
    ma, mb = a.mean(axis=0), b.mean(axis=0)
    a = (a-ma)/np.sqrt(len(a)-1);b = (b-mb)/np.sqrt(len(b)-1)
    value = np.sum((ma-mb)**2) + np.sum(a*a) + np.sum(b*b) - 2*np.linalg.svd(a@b.T, compute_uv=False).sum()
    return float(max(0,value))


def matched_references(directory, cases):
    expected = {c['generated_video_name']: c['id'] for c in cases}
    matches = {}
    for p in directory.glob('*_take-1_*.mp4'):
        name=p.name.replace('_testing-videos_24FPS_', '_', 1).replace('_take-1_', '_', 1)
        if name in expected:
            key=expected[name]
            if key in matches:raise ValueError(f'Duplicate official take-1 video: {key}')
            matches[key]=p
    if set(matches)!=set(expected.values()):
        raise ValueError('Physics-IQ reference pool does not match all 198 pinned identities')
    return matches


def sample(source, frames, fps):
    """Same 16-frame / short-edge 224 / center-crop convention as PhyGen I3D."""
    import cv2
    out=[]; cap=None; tensor=None
    indices=np.linspace(0,frames-1,16).astype(int)
    if source.is_file():
        cap=cv2.VideoCapture(str(source))
        n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));rate=float(cap.get(cv2.CAP_PROP_FPS))
        if n<frames or not np.isclose(rate,fps,atol=.01):
            cap.release();raise ValueError(f'Video does not match the declared decoder grid: {source.name}')
    elif not all((source/'workspace/frames'/f'{i:02d}.png').is_file() for i in indices):
        # Minimal RGB deliveries can retain the lossless packed tensor without
        # a second PNG copy. Decode the same RGB pixels before the unchanged
        # temporal sampling, resize and crop; never fill missing video frames.
        with np.load(source/'workspace/pixels.npz',allow_pickle=False) as archive:
            if archive.files != ['pixels']:
                raise ValueError('Expected a single packed RGB pixel tensor')
            tensor=archive['pixels']
        if (tensor.dtype != np.uint32 or tensor.ndim != 3
                or tensor.shape[2] != frames or min(tensor.shape[:2]) < 1):
            raise ValueError('Packed RGB tensor does not match the declared frame grid')
    try:
        for i in indices:
            if tensor is not None:
                packed=tensor[:,:,i]
                rgb=Image.fromarray(np.stack([(packed>>16)&255,(packed>>8)&255,
                                             packed&255],axis=-1).astype(np.uint8))
            elif cap is None:
                with Image.open(source/'workspace/frames'/f'{i:02d}.png') as im:rgb=im.convert('RGB')
            else:
                cap.set(cv2.CAP_PROP_POS_FRAMES,int(i));ok,data=cap.read()
                if not ok:raise ValueError(f'Cannot decode reference frame {i}')
                rgb=Image.fromarray(cv2.cvtColor(data,cv2.COLOR_BGR2RGB))
            w,h=rgb.size;scale=224/min(w,h)
            rgb=rgb.resize((round(w*scale),round(h*scale)),Image.Resampling.BILINEAR)
            x,y=(rgb.width-224)//2,(rgb.height-224)//2
            out.append(np.asarray(rgb.crop((x,y,x+224,y+224))).copy())
    finally:
        if cap is not None:cap.release()
    return np.stack(out)


def extract(net, torch, source, identity, frames, fps, cache, device, weights):
    cache_key=hashlib.sha256(json.dumps([VERSION,identity,frames,fps,weights]).encode()).hexdigest()
    path=cache/f'{cache_key}.npy'
    if path.is_file():
        return np.load(path,allow_pickle=False)
    rgb=sample(source,frames,fps)
    x=torch.from_numpy(rgb).to(device=device,dtype=torch.float32).permute(3,0,1,2).unsqueeze(0).contiguous()
    with torch.inference_mode():feature=net(x,rescale=True,resize=True,return_features=True).reshape(-1).cpu().numpy()
    if not np.isfinite(feature).all():raise ValueError('Nonfinite I3D output')
    tmp=path.with_suffix('.tmp')
    with tmp.open('wb')as f:np.save(f,feature)
    tmp.replace(path)
    return feature


def main():
    import torch
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--references',type=Path,required=True);p.add_argument('--i3d',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--smoke',action='store_true');p.add_argument('--models',nargs='+');a=p.parse_args()
    if a.output.exists():raise ValueError('Output must be new to avoid mixing dataset scopes')
    torch.set_num_threads(2)
    cases=official_cases();refs=matched_references(a.references,cases);ids=[c['id']for c in cases]
    scope=set(ids); videos={}
    for row in json.loads(a.manifest.read_text())['videos']:
        if row['benchmark']!=BENCHMARK:continue
        group=videos.setdefault(row['model'],{});case=row['case']
        if case not in scope or case in group or (row['frames'],row['fps'])!=(120,24):raise ValueError('Invalid generated case identity or media grid')
        group[case]=row
    models=sorted(a.models or videos);allowed={m:videos[m]for m in models if set(videos[m])==scope}
    excluded={m:sorted(scope-set(videos[m]))for m in models if m not in allowed}
    if not allowed:raise ValueError('No complete matched cohort')
    if a.smoke:ids=ids[:2]
    a.output.mkdir(parents=True,mode=0o700);cache=a.output/'features';cache.mkdir()
    w=sha256(a.i3d);net=torch.jit.load(a.i3d,map_location=a.device).eval()
    meta={'protocol':VERSION,'i3d_sha256':w,'case_ids':ids,'full198':not a.smoke,'excluded_incomplete_models':excluded,
          'references':{k:refs[k].name for k in ids},'manifest_sha256':sha256(a.manifest),'device':a.device,'metric':'distribution distance; not a physical or subjective judge score'}
    atomic_json(a.output/'execution.json',{**meta,'phase':'extracting_reference','done':0})
    reference=[]
    for i in ids:
        pth=refs[i];ref_identity=f'{pth.name}:{pth.stat().st_size}:{pth.stat().st_mtime_ns}'
        reference.append(extract(net,torch,pth,ref_identity,120,24,cache,a.device,w))
        atomic_json(a.output/'execution.json',{**meta,'phase':'extracting_reference','done':len(reference)})
    scores={}
    for m,group in allowed.items():
        data=[]
        for i in ids:
            row=group[i];data.append(extract(net,torch,Path(row['source']),row['identity'],120,24,cache,a.device,w))
            atomic_json(a.output/'execution.json',{**meta,'phase':'extracting_predictions','model':m,'done':len(data),'completed_models':list(scores)})
        scores[m]={'fvd':frechet(np.stack(reference),np.stack(data)),'count':len(data)}
        atomic_json(a.output/'summary.json',{**meta,'scores':scores})
    atomic_json(a.output/'execution.json',{**meta,'phase':'finished','scores':scores,'finished_utc':utc_now()})

if __name__=='__main__':main()
