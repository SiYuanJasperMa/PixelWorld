"""Calibrate the pinned PCVE physics evaluator on source-copy and edited-oracle videos.

No generation model is called. Controls reuse the official evaluation-side
videos and run unchanged physics scoring, including tracking and PES floors.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from pixelworld import pcve
from pixelworld.util import atomic_json,sha256,utc_now
from tools.run_pcve_gpu_worker import environment
from tools.pcve_native import native_setup,native_cache


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--control',choices=['source_copy','edited_oracle'],required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--limit',type=int)
    p.add_argument('--sam2-native-dir',type=Path)
    p.add_argument('--all-metrics',action='store_true',help='Also run official visual fidelity and FVD metrics')
    args=p.parse_args();code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve();output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    data=code/'data/pcve-rigid';source=code/'vendor/pcve-rigid-eval-pinned'
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if revision!=pcve.EVALUATOR_REVISION or subprocess.run(['git','-C',str(source),'diff','--quiet','HEAD']).returncode:
        raise ValueError('Unmodified pinned PCVE evaluator required')
    manifest=data/'benchmark_manifest.json'
    if sha256(manifest)!=pcve.MANIFEST_SHA256:raise ValueError('PCVE dataset manifest mismatch')
    edits=json.loads(manifest.read_text())['edits']
    predictions=output/'predictions';baseline='control_'+args.control
    for row in edits:
        target=predictions/baseline/'videos'/row['scene']/(row['case_id']+'.mp4')
        video=data/row['source_video'if args.control=='source_copy'else'edited_video']
        if not video.is_file():raise FileNotFoundError(video)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():target.symlink_to(video.resolve())
    checkpoint=code/'vendor/pcve-eval-models/sam2.1_hiera_large.pt'
    if not checkpoint.is_file():raise FileNotFoundError(checkpoint)
    driver=output/'official_control_driver.py'
    argv=[str(source/'eval/compute_metrics.py'),'--benchmark-root',str(data),
          '--predictions-root',str(predictions),'--baseline',baseline,'--device','cuda']
    if not args.all_metrics:argv+=['--physics-only','--no-fvd']
    if args.limit is not None:argv+=['--limit',str(args.limit)]
    setup=native_setup(args.sam2_native_dir,native_cache(bundle,args.sam2_native_dir)) if args.sam2_native_dir else ''
    i3d=code/'vendor/pcve-eval-models/i3d_torchscript.pt'
    driver.write_text('import sys,runpy,os,functools,open_clip\n'+
        'open_clip.create_model_and_transforms=functools.partial(open_clip.create_model_and_transforms,cache_dir=os.path.join(os.environ["HF_HOME"],"open_clip"))\n'+
        f'sys.path.insert(0,{str(source/"eval")!r})\n'+setup+
        'from metrics import fvd\n'+
        f'fvd.I3DFeatures.__init__.__defaults__=({str(i3d)!r},)+fvd.I3DFeatures.__init__.__defaults__[1:]\n'+
        'from metrics import grounded_sam2_tracker as tracker\n'+
        f'tracker.GroundedSam2Tracker.__init__.__defaults__=({str(checkpoint)!r},)+tracker.GroundedSam2Tracker.__init__.__defaults__[1:]\n'+
        f'sys.argv={argv!r}\n'+f'runpy.run_path({str(source/"eval/compute_metrics.py")!r},run_name="__main__")\n')
    env=environment(code,bundle,output);env['IMAGEIO_FFMPEG_EXE']=str(bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg')
    record={'control':args.control,'source_revision':revision,'dataset_revision':pcve.DATASET_REVISION,
            'expected_cases':args.limit or 129,'expected_pes':0 if args.control=='source_copy' else 1,
            'started_utc':utc_now(),'scoring_code_unchanged':True,'checkpoint_path_only_overridden':True,
            'sam2_native_postprocessing':bool(args.sam2_native_dir),'all_metrics':args.all_metrics,
            'sam2_native_extension_sha256':sha256(args.sam2_native_dir/'_C.so') if args.sam2_native_dir else None}
    atomic_json(output/'control_plan.json',record)
    result=subprocess.call([sys.executable,str(driver)],cwd=source/'eval',env=env)
    record.update(returncode=result,completed_utc=utc_now())
    if result==0:
        metrics=json.loads((predictions/baseline/'metrics.json').read_text())
        rows=metrics['per_case'];values=[r['pes']for r in rows if r.get('pes')is not None]
        record.update(scored_cases=len(values),scores=metrics['aggregate'],mean_pes=sum(values)/len(values)if values else None,
                      per_case_pes={r['global_id']:r.get('pes')for r in rows},metrics_sha256=sha256(predictions/baseline/'metrics.json'))
    atomic_json(output/'control_result.json',record)
    raise SystemExit(result)


if __name__=='__main__':main()
