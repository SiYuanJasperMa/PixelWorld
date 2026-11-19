"""Score local H3 PCVE predictions with the same pinned released evaluator."""
import argparse
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from pixelworld import pcve
from pixelworld.util import atomic_json,sha256,utc_now
from tools.evaluate_pcve_full import incomplete_physics_cases,finite_number
from tools.pcve_native import native_directory,native_setup,native_cache
from tools.run_pcve_gpu_worker import environment


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--limit',type=int);p.add_argument('--subset',type=Path);args=p.parse_args();code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve()
    base=bundle/'baselines/minimax_h3_local';output=base/('smoke_evaluation'if args.limit else'evaluation_subset'if args.subset else'evaluation');output.mkdir(parents=True,exist_ok=True)
    source=code/'vendor/pcve-rigid-eval-pinned';data=code/'data/pcve-rigid';native=native_directory(bundle)
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if revision!=pcve.EVALUATOR_REVISION or subprocess.run(['git','-C',str(source),'diff','--quiet','HEAD']).returncode:raise ValueError('Pinned unchanged evaluator required')
    if sha256(data/'benchmark_manifest.json')!=pcve.MANIFEST_SHA256:raise ValueError('Manifest pin mismatch')
    manifest=json.loads((data/'benchmark_manifest.json').read_text());edits=manifest['edits']
    indices=json.loads(args.subset.read_text())['indices']if args.subset else list(range(len(edits)))
    if len(indices)!=len(set(indices))or any(not isinstance(i,int)or not 0<=i<len(edits)for i in indices):raise ValueError('Invalid subset indices')
    selected=[edits[i]for i in indices];selected=selected[:args.limit]if args.limit else selected
    evaluation_data=data
    if args.subset:
        evaluation_data=output/'benchmark_subset';evaluation_data.mkdir(exist_ok=True)
        scenes=evaluation_data/'scenes'
        if not scenes.exists():scenes.symlink_to(data/'scenes',target_is_directory=True)
        atomic_json(evaluation_data/'benchmark_manifest.json',{**manifest,'edits':selected})
    for row in selected:
        video=base/'predictions/minimax_h3_local/videos'/row['scene']/(row['case_id']+'.mp4')
        if not video.is_file():raise ValueError('Wait for all required prediction videos')
    driver=output/'official_driver.py';argv=[str(source/'eval/compute_metrics.py'),'--benchmark-root',str(evaluation_data),
        '--predictions-root',str(base/'predictions'),'--baseline','minimax_h3_local','--device','cuda']
    if args.limit:argv+=['--limit',str(args.limit),'--no-fvd']
    driver.write_text('import sys,runpy,os,functools,open_clip\n'+
        'open_clip.create_model_and_transforms=functools.partial(open_clip.create_model_and_transforms,cache_dir=os.path.join(os.environ["HF_HOME"],"open_clip"))\n'+
        f'sys.path.insert(0,{str(source/"eval")!r})\n'+native_setup(native,native_cache(bundle,native))+
        'from metrics import grounded_sam2_tracker as tracker,fvd\n'+
        f'tracker.GroundedSam2Tracker.__init__.__defaults__=({str(code/"vendor/pcve-eval-models/sam2.1_hiera_large.pt")!r},)+tracker.GroundedSam2Tracker.__init__.__defaults__[1:]\n'+
        f'fvd.I3DFeatures.__init__.__defaults__=({str(code/"vendor/pcve-eval-models/i3d_torchscript.pt")!r},)+fvd.I3DFeatures.__init__.__defaults__[1:]\n'+
        f'sys.argv={argv!r}\nrunpy.run_path({str(source/"eval/compute_metrics.py")!r},run_name="__main__")\n')
    env=environment(code,bundle,output);env['IMAGEIO_FFMPEG_EXE']=str(bundle.parent/'bench_codex_20261109/runtime_main_worker_20261116/bin/ffmpeg')
    state={'phase':'official_evaluation','expected':len(selected),'completed':0,'updated_utc':utc_now()};atomic_json(output/'execution.json',state)
    process=subprocess.Popen([sys.executable,'-u',str(driver)],cwd=source/'eval',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    for line in process.stdout:
        sys.stdout.write(line);sys.stdout.flush();m=re.match(r'^\[(\d+)/(\d+)\]',line)
        if m:state.update(completed=int(m[1]),updated_utc=utc_now());atomic_json(output/'execution.json',state)
    status=process.wait();state.update(phase='finished'if status==0 else'failed',returncode=status,updated_utc=utc_now());atomic_json(output/'execution.json',state)
    if status:raise RuntimeError('Released evaluator failed')
    metric_file=base/'predictions/minimax_h3_local/metrics.json';metrics=json.loads(metric_file.read_text());rows=metrics['per_case']
    if len(rows)!=len(selected)or {r['global_id']for r in rows}!={r['global_id']for r in selected}or any(r['status']!='completed'for r in rows)or incomplete_physics_cases(rows):raise ValueError('Incomplete per-case evaluation')
    aggregate=metrics['aggregate'];fields=['pes','traj_px','mask_iou','psnr','ssim','lpips','clip_sim']
    if any(not finite_number(aggregate['overall'].get(k))for k in fields)or(not args.limit and not finite_number(aggregate.get('fvd'))):raise ValueError('Incomplete metrics')
    atomic_json(output/'metrics.json',metrics)
    atomic_json(output/'summary.json',{'model':'minimax-h3-local','track':'video-edit','case_count':len(rows),
        'complete_official129':not args.limit and not args.subset and len(rows)==129,'scores':aggregate,'source_revision':revision,
        'dataset_revision':pcve.DATASET_REVISION,'fps':24,'frames':96,'resolution':[1344,768],
        'official_metrics_sha256':sha256(output/'metrics.json'),'sam2_native_postprocessing':True,
        'subset_indices':indices if args.subset else None,'original_manifest_sha256':pcve.MANIFEST_SHA256,
        'sam2_native_extension_sha256':sha256(native/'_C.so'),'validated_numeric_physics_cases':len(rows),
        'generation_model':'MiniMaxAI/MiniMax-H3','generation_revision':'42ed227ee7df40d41602854ae760620d6eb651fe',
        'published_table1_comparability_verified':False,'published_minimax_api_equivalence_verified':False,
        'evaluation_label':'Local official checkpoint; same released evaluator as coding agents','completed_utc':utc_now()})


if __name__=='__main__':main()
