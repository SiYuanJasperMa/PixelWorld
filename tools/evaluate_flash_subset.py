"""Score saved Flash trial videos with case-specific masks and pinned Physics-IQ code."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from pixelworld.constants import (PHYSICS_IQ_COMMIT, PHYSICS_IQ_DATASET_REVISION,
                                 PHYSICS_IQ_DESCRIPTIONS_SHA256)
from tools.evaluation_memory import check_pool_memory


def one_match(folder, pattern):
    paths = list(folder.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f'Expected one mask/video match for {pattern}; found {len(paths)}')
    return paths[0]


def score(job):
    sys.path.insert(0, job['upstream'])
    from physiq.binary_mask_generator import generate_mask
    from physiq.calculate_and_write_metrics_to_csv import ViewPaths, process_view
    video = Path(job['video'])
    identifier, view, scenario = video.name.split('_', 2)
    dataset = Path(job['dataset'])
    fps=job.get('fps',4);frames=job.get('frames',20)
    real = dataset / f'split-videos/testing/{fps}FPS'
    masks = dataset / f'video-masks/real/{fps}FPS'
    take1 = one_match(real, f'{identifier}_*_{view}_take-1_{scenario}')
    take2 = one_match(real, f'*_{view}_take-2_{scenario}')
    directory = Path(job['mask_dir']) / job['case']
    directory.mkdir(parents=True, exist_ok=True)
    generate_mask(str(video), str(directory / video.name), False)
    generated = one_match(directory, f'{identifier}_video-masks_{fps}FPS_{view}_*.mp4')
    paths = ViewPaths(str(take1), str(take2), str(video),
        str(masks / take1.name.replace('testing-videos', 'video-masks')),
        str(masks / take2.name.replace('testing-videos', 'video-masks')), str(generated))
    result = process_view(paths, view, 0, frames, frames)
    if result is None:
        raise ValueError('Official process_view returned no metrics')
    return {'arm': job['arm'], 'case': job['case'], 'view': view,
        'v1': float(result[f'weighted_spatial_iou_v1_{view}']),
        'v2': float(result[f'weighted_spatial_iou_v2_{view}']),
        'pixels_sha256': job['pixels_sha256'], 'metrics': result,
        'scoring_fps':fps,'scoring_frames':frames}


def export(run, native, sampled, ffmpeg, spec):
    native.parent.mkdir(parents=True, exist_ok=True)
    if sampled is not None:sampled.parent.mkdir(parents=True, exist_ok=True)
    if not native.exists():
        temporary = native.with_name(native.stem + '.writing.mp4')
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-framerate', str(spec['fps']),
            '-i', str(run / 'workspace/frames/%02d.png'), '-frames:v', str(spec['frames']),
            '-an', '-c:v', 'libx264rgb', '-preset', 'ultrafast', '-threads', '1',
            '-crf', '0', '-pix_fmt', 'rgb24', str(temporary)], check=True)
        temporary.replace(native)
    if sampled is not None and not sampled.exists():
        ratio = float(spec['fps']) / 4
        if ratio != int(ratio):
            raise ValueError('This adapter requires integer decimation to canonical 4 FPS')
        temporary = sampled.with_name(sampled.stem + '.writing.mp4')
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', str(native),
            '-vf', f"select='not(mod(n,{int(ratio)}))',setpts=N/(4*TB)",
            '-r', '4', '-an', '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv444p',
            '-threads', '1', str(temporary)], check=True)
        temporary.replace(sampled)


def update(cohort, resource, ffmpeg, model_key, workers, native_scoring=False):
    memory=check_pool_memory(workers,120 if native_scoring else 20)
    print('Evaluator memory',json.dumps(memory),flush=True)
    upstream = resource / 'upstream/physics-IQ-benchmark'
    revision = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PHYSICS_IQ_COMMIT:
        raise ValueError('Official evaluator pin mismatch')
    if subprocess.check_output(['git', '-C', str(upstream), 'diff', 'HEAD', '--name-only'], text=True).strip():
        raise ValueError('Official evaluator has tracked modifications')
    descriptions = resource / 'protocol/descriptions_base.csv'
    if hashlib.sha256(descriptions.read_bytes()).hexdigest() != PHYSICS_IQ_DESCRIPTIONS_SHA256:
        raise ValueError('Official descriptions pin mismatch')
    with descriptions.open() as handle:
        records = {row['generated_video_name'][:4]: row for row in csv.DictReader(handle)
                   if '_take-1_' in row['scenario']}
    manifest = json.loads((cohort / 'experiment_manifest.json').read_text())
    output = cohort / 'quality'
    output.mkdir(exist_ok=True)
    checkpoint = output / ('flash_quality_native24_checkpoint.json' if native_scoring else 'flash_quality_checkpoint_v2.json')
    results = json.loads(checkpoint.read_text()).get('per_case', []) if checkpoint.exists() else []
    existing = {(r['arm'], r['case']): r for r in results}
    jobs = []
    for arm in manifest['arms']:
        for case in manifest['case_ids']:
            run = cohort / 'generations' / arm / model_key / 'vision-agent' / f'case_{case}_attempt_01'
            path = run / 'run_manifest.json'
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if record.get('status') != 'complete' or not record.get('validation', {}).get('pass'):
                continue
            identity = (arm, case)
            previous = existing.get(identity)
            if previous and previous.get('pixels_sha256') == record.get('pixels_sha256'):
                continue
            if previous:
                raise ValueError('Previously scored source pixels changed')
            spec = json.loads((run / 'media_contract.json').read_text())
            if native_scoring and (spec.get('fps')!=24 or spec.get('frames')!=120):
                raise ValueError('Native24 subset adapter requires the shared24FPS/120-frame contract')
            name = records[case]['generated_video_name']
            native = output / 'native_videos' / arm / name
            if native_scoring and not native.exists():
                # Separate staging avoids colliding with the existing4FPS
                # adapter when a newly delivered case is exported concurrently.
                native=output/'native_score_videos'/arm/name
            sampled = None if native_scoring else output / 'videos_4fps' / arm / name
            export(run, native, sampled, ffmpeg, spec)
            fps=int(spec['fps']) if native_scoring else 4
            frames=int(spec['frames']) if native_scoring else 20
            jobs.append({'arm': arm, 'case': case, 'video': str(native if native_scoring else sampled),
                'fps':fps,'frames':frames,
                'upstream': str(upstream.resolve()),
                'dataset': str((resource / 'data/physics-IQ-benchmark-verified').resolve()),
                'mask_dir': str(output / ('masks_native24' if native_scoring else 'masks') / arm), 'pixels_sha256': record.get('pixels_sha256')})
    print('new validated subset videos', len(jobs), flush=True)
    pilot_cases={case for ids in manifest.get('arm_case_ids',{}).values() if len(ids)==1 for case in ids}
    jobs.sort(key=lambda job:(job['case'] not in pilot_cases,job['arm']!='baseline',job['arm'],job['case']))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(score, jobs):
            results.append(row)
            report = {'scope': 'Independent Physics-IQ subset; not the full 198-case Verified score',
                'official_commit': revision, 'dataset_revision_pin': PHYSICS_IQ_DATASET_REVISION,
                'mask_assignment': 'per-case-v2', 'per_case': results}
            report.update(scoring_fps=24 if native_scoring else 4,
                          scoring_frames=120 if native_scoring else 20,
                          temporal_sampling='native submission frames' if native_scoring else 'integer decimation to4FPS')
            temporary = checkpoint.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, indent=2, default=float))
            temporary.replace(checkpoint)
            print(row['arm'], row['case'], row['v1'], row['v2'], flush=True)
    return len(results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--resource-root', type=Path, required=True)
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    parser.add_argument('--model-key', default='gpt56-luna')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=120)
    parser.add_argument('--native-scoring',action='store_true',help='Score all120 native24FPS frames with matching official references, retaining separate4FPS development results')
    args = parser.parse_args()
    if not args.ffmpeg:
        raise ValueError('Provide the existing ffmpeg executable')
    while True:
        try:
            print('scored', update(args.cohort, args.resource_root, args.ffmpeg, args.model_key, args.workers,args.native_scoring), flush=True)
        except Exception as error:
            print(type(error).__name__, str(error), flush=True)
            if not args.watch:
                raise
        if not args.watch:
            break
        time.sleep(args.interval)
