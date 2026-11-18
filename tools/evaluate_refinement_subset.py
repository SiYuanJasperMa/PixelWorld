"""Compare saved first deliverable RGB candidates with final videos; no generation."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from PIL import Image

from pixelworld.util import atomic_json,sha256
from tools.evaluate_flash_subset import score
from tools.watch_physics_evaluation import validate_runtime,validate_reference_fps


def first_deliverable(run):
    snapshots=[]
    for path in sorted((run/'trace').glob('round_*/snapshot.json')):
        record=json.loads(path.read_text());checks=record.get('artifact_checks',{})
        if (record.get('required_output_files_complete') and checks.get('frame0_exact')
                and checks.get('all_png_tensor_exact') and checks.get('copied_code_and_tensor_match_source')):
            if sha256(path.parent/'pixels.npz')!=record['pixels_sha256']:
                raise ValueError('Saved first-candidate tensor changed')
            if sha256(path.parent/'generate.py')!=record['generate_sha256']:
                raise ValueError('Saved first-candidate code changed')
            snapshots.append((path.parent,record))
    return snapshots[0] if snapshots else None


def evaluate(cohort,resource,ffmpeg,case):
    runtime=validate_runtime(resource=resource,python_override=Path(sys.executable))
    validate_reference_fps(runtime,24)
    run=cohort/f'generations/baseline/gpt56-luna/vision-agent/case_{case}_attempt_01'
    manifest=json.loads((run/'run_manifest.json').read_text())
    if manifest.get('status')!='complete' or not manifest.get('validation',{}).get('pass'):
        raise ValueError('Refinement analysis requires a valid final delivery')
    first=first_deliverable(run)
    if first is None:return {'case':case,'status':'missing_first_deliverable_snapshot'}
    folder,record=first
    if record['media_contract']!=manifest['media_contract']:
        raise ValueError('First candidate has a different media contract')
    with Image.open(run/'input_snapshot/first_frame.png') as image:
        source=np.asarray(image.convert('RGB'))
    with Image.open(folder/'frames/00.png') as image:
        if not np.array_equal(source,np.asarray(image.convert('RGB'))):
            raise ValueError('First candidate changed its conditioning frame')
    final_scores=json.loads((cohort/'quality/flash_quality_native24_checkpoint.json').read_text())['per_case']
    final=next(x for x in final_scores if x['arm']=='baseline' and x['case']==case)
    if final['pixels_sha256']!=manifest['pixels_sha256']:
        raise ValueError('Final scored pixels no longer match the delivered run')
    if record['pixels_sha256']==manifest['pixels_sha256']:
        first_score=final
    else:
        with (resource/'protocol/descriptions_base.csv').open() as file:
            row=next(x for x in csv.DictReader(file)if x['generated_video_name'].startswith(case+'_'))
        spec=record['media_contract'];assert (spec['fps'],spec['frames'])==(24,120)
        video=cohort/'quality/refinement_first_videos'/row['generated_video_name'];video.parent.mkdir(parents=True,exist_ok=True)
        if not video.exists():
            temporary=video.with_name(video.stem+'.writing.mp4')
            subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-framerate','24','-i',str(folder/'frames/%02d.png'),
                '-frames:v','120','-an','-c:v','libx264rgb','-preset','ultrafast','-threads','1','-crf','0','-pix_fmt','rgb24',str(temporary)],check=True)
            temporary.replace(video)
        first_score=score({'arm':'baseline_first','case':case,'video':str(video),
            'upstream':str(resource/'upstream/physics-IQ-benchmark'),'dataset':str(resource/'data/physics-IQ-benchmark-verified'),
            'mask_dir':str(cohort/'quality/refinement_first_masks'),'fps':24,'frames':120,'pixels_sha256':record['pixels_sha256']})
    return {'case':case,'status':'scored','first_snapshot_round':record['round'],
        'first_pixels_sha256':record['pixels_sha256'],'final_pixels_sha256':manifest['pixels_sha256'],
        'first_v1':first_score['v1'],'first_v2':first_score['v2'],'final_v1':final['v1'],'final_v2':final['v2'],
        'delta_v1':final['v1']-first_score['v1'],'delta_v2':final['v2']-first_score['v2'],
        'sampling':'native24FPS/120frames','causal_claim':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort',type=Path,required=True)
    parser.add_argument('--resource-root',type=Path,required=True)
    parser.add_argument('--ffmpeg',required=True)
    parser.add_argument('--case',action='append')
    args=parser.parse_args();cases=args.case or json.loads((args.cohort/'experiment_manifest.json').read_text())['case_ids']
    output=args.cohort/'quality/refinement_native24.json'
    data=json.loads(output.read_text()) if output.exists() else {'scope':'Descriptive first-deliverable versus final candidates on baseline development cases','per_case':[]}
    done={x['case'] for x in data['per_case']}
    for case in cases:
        if case in done:continue
        row=evaluate(args.cohort,args.resource_root,args.ffmpeg,case);data['per_case'].append(row)
        atomic_json(output,data);print(row,flush=True)
