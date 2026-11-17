#!/usr/bin/env python3
"""Evaluator-side handoff for complete198-input Physics-IQ Verified cohorts.

No generation, reference feedback, missing-output imputation, or baseline scoring.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixelworld.config import load_config
from pixelworld.constants import PHYSICS_IQ_COMMIT, PHYSICS_IQ_DATASET_REVISION, PHYSICS_IQ_DESCRIPTIONS_SHA256
from pixelworld.physics_iq import export_videos, official_cases
from pixelworld.supervisor import inspect
from pixelworld.util import atomic_json, sha256, utc_now


def validate_runtime(resource: Path | None = None, evaluator_ready: Path | None = None,
                     python_override: Path | None = None) -> dict:
    if (resource is None) == (evaluator_ready is None):
        raise ValueError('Select either legacy resource-root or the repository setup ready marker')
    if evaluator_ready is not None:
        ready = json.loads(evaluator_ready.read_text())
        if (ready.get('upstream_commit') != PHYSICS_IQ_COMMIT
                or ready.get('dataset_revision') != PHYSICS_IQ_DATASET_REVISION):
            raise ValueError('Setup ready marker has a different official source/dataset revision')
        source = Path(ready['upstream']).resolve()
        # Resolving a venv interpreter symlink would discard its environment.
        python = Path(ready.get('python',source/'.venv/bin/python')).absolute()
        descriptions = source/'descriptions/best_practice/descriptions_base.csv'
        benchmark_parent = Path(ready['benchmark_parent']).resolve()
        dataset_path = Path(ready['dataset']).resolve()
        if dataset_path != (benchmark_parent/'physics-IQ-benchmark-verified').resolve():
            raise ValueError('Setup ready marker dataset does not belong to its benchmark parent')
        revision = ready['dataset_revision']
        bin_dir = python.parent
    else:
        source = resource/'upstream/physics-IQ-benchmark'
        python = resource/'.venv/bin/python'
        descriptions = resource/'protocol/descriptions_base.csv'
        revision = json.loads((resource/'protocol/dataset.json').read_text()).get('revision')
        benchmark_parent = resource/'data'
        dataset_path = benchmark_parent/'physics-IQ-benchmark-verified'
        bin_dir = resource/'tools/bin'
    if python_override is not None:
        python = python_override.absolute()
    if (not python.is_file() or revision != PHYSICS_IQ_DATASET_REVISION
            or sha256(descriptions) != PHYSICS_IQ_DESCRIPTIONS_SHA256):
        raise ValueError('Original evaluator runtime/data/description identity mismatch')
    commit = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    dirty = subprocess.check_output(['git','-C',str(source),'diff','HEAD','--name-only'],text=True).strip()
    if commit != PHYSICS_IQ_COMMIT or dirty:
        raise ValueError('Original evaluator source is not the pinned pristine commit')
    if not dataset_path.is_dir():
        raise FileNotFoundError('Verified evaluation reference data missing')
    return {'source':source,'python':python,'descriptions':descriptions,
            'benchmark_parent':benchmark_parent,'bin_dir':bin_dir}


def probe_runtime(runtime: dict) -> dict:
    env = os.environ.copy()
    env['PYTHONPATH'] = str(runtime['source'])
    command = [str(runtime['python']), '-c',
        'import json,sys,cv2,numpy,pandas,scipy,matplotlib; '
        'import physiq.run_physics_iq as evaluator; '
        'print(json.dumps({"python":sys.executable,"version":sys.version,'
        '"cv2":cv2.__version__,"numpy":numpy.__version__,"pandas":pandas.__version__,'
        '"scipy":scipy.__version__,"matplotlib":matplotlib.__version__,'
        '"evaluator":evaluator.__file__}))']
    result = subprocess.run(command, cwd=runtime['source'], env=env,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Evaluator dependency preflight failed: ' + result.stderr[-2000:])
    record = json.loads(result.stdout.splitlines()[-1])
    if not Path(record['evaluator']).resolve().is_relative_to(runtime['source'].resolve()):
        raise ValueError('Dependency probe imported an unrelated evaluator')
    return record


def validate_reference_fps(runtime: dict, fps: int) -> None:
    with runtime['descriptions'].open() as handle:
        rows = list(csv.DictReader(handle))
    dataset = runtime['benchmark_parent'] / 'physics-IQ-benchmark-verified'
    for kind, rate, directory in [('testing-videos', 30, 'split-videos/testing/30FPS'),
                                  ('testing-videos', fps, f'split-videos/testing/{fps}FPS'),
                                  ('video-masks', fps, f'video-masks/real/{fps}FPS')]:
        expected = set()
        for row in rows:
            identifier, view, take, scenario = row['scenario'].split('_')
            expected.add(f'{identifier}_{kind}_{rate}FPS_{view}_{take}_{scenario}')
        folder = dataset / directory
        actual = {p.name for p in folder.glob('*.mp4') if p.stat().st_size > 0}
        missing = expected - actual
        if missing:
            raise RuntimeError(f'Pinned reference resources incomplete: {directory}: '
                               f'{len(missing)} missing of {len(expected)}')


def exported_videos(config, model, track: str, output: Path) -> Path:
    """Reuse a complete, unchanged lossless export after evaluator-only failures."""
    manifest_path = output / 'input_manifest.json'
    if not manifest_path.exists():
        return export_videos(config, model.key, track, output)
    record = json.loads(manifest_path.read_text())
    cases = official_cases()
    rows = record.get('videos', [])
    if (record.get('model_key') != model.key or record.get('track') != track
            or record.get('count') != 198 or len(rows) != 198
            or {r.get('id') for r in rows} != {c['id'] for c in cases}):
        raise ValueError('Existing export has a different cohort identity or scope')
    directory = output / f'pixelworld-{model.key}-{track}-bpp-run_01'
    expected = {c['id']: c for c in cases}
    for row in rows:
        case = expected[row['id']]
        info = inspect(config, model, track, row['id'])
        run = Path(info['run']) if info.get('run') else None
        if (info.get('state') != 'complete' or run is None
                or run.resolve() != Path(row['source_run']).resolve()
                or not row.get('decoded_rgb_exact')
                or row.get('base_bpp_sha256') != case['prompt_sha256']
                or row.get('filename') != case['generated_video_name']):
            raise ValueError('Existing export no longer matches current accepted generation')
        for path, digest in [(run/'run_manifest.json', row['source_run_manifest_sha256']),
                             (run/'workspace/pixels.npz', row['source_pixels_sha256']),
                             (directory/row['filename'], row['video_sha256'])]:
            if not digest or not path.is_file() or sha256(path) != digest:
                raise ValueError('Existing export source or video changed; preserving old export')
        spec = row['media_contract']
        if ([spec['width'], spec['height']] != record['resolution']
                or spec['fps'] != record['fps'] or spec['frames'] != record['frames']
                or spec['frames']/spec['fps'] != record['duration_seconds']):
            raise ValueError('Existing export media contract is inconsistent')
    return directory


def step(config, resource: Path | None, output: Path, states: dict, n_process: int = 4,
         evaluator_ready: Path | None = None, python_override: Path | None = None) -> dict:
    cases = official_cases()
    progress_path = config.runs_root/'status.json'
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    for model in config.models:
        for track in model.tracks:
            key = f'{model.key}/{track}'
            if states.get(key, {}).get('phase') in ('complete','failed'):
                continue
            if states.get(key, {}).get('phase') == 'incomplete_official_scope':
                current = progress.get('groups', {}).get(key, {}).get('counts', {})
                if (current and current.get('complete', 0) < 198
                        and not current.get('pending', 0) and not current.get('running', 0)):
                    continue
            entry = {'model':model.key,'track':track,'expected_cases':198,'official_full_score':False,
                     'phase':'waiting_for_generation'}
            try:
                counts = progress.get('groups',{}).get(key,{}).get('counts')
                if counts is not None and counts.get('complete',0) < 198:
                    terminal_hint = (sum(counts.values()) == 198 and not counts.get('pending',0)
                                     and not counts.get('running',0))
                    if not terminal_hint:
                        entry.update(accepted_cases=counts.get('complete',0),exhausted_cases=counts.get('exhausted',0),
                                     generation_status_utc=progress.get('updated_utc'))
                        states[key]=entry
                        continue
                    # Even a terminal hint is rechecked against all actual runs.
                    # A previous smoke status must not terminalize a new full run.
                inventory = [inspect(config,model,track,case['id']) for case in cases]
                missing = [item for item in inventory if item['state'] != 'complete']
                entry.update(accepted_cases=198-len(missing),exhausted_cases=sum(item['state']=='exhausted' for item in missing))
                if missing:
                    if all(item['state']=='exhausted' for item in missing):entry['phase']='incomplete_official_scope'
                    states[key]=entry
                    continue
                cohort = output/model.key/track
                score_path = cohort/'scores.json'
                if score_path.exists():
                    score = json.loads(score_path.read_text())
                    if (score.get('inputs') != 198 or score.get('upstream_commit') != PHYSICS_IQ_COMMIT
                            or score.get('dataset_revision') != PHYSICS_IQ_DATASET_REVISION):
                        raise ValueError('Existing score does not attest complete pinned scope')
                else:
                    runtime = validate_runtime(resource,evaluator_ready,python_override)
                    if any(runtime[k].is_relative_to(config.runs_root.resolve()) for k in ('source','benchmark_parent')):
                        raise ValueError('Evaluator resources must remain outside generation workspaces')
                    run_spec = json.loads((Path(inventory[0]['run'])/'media_contract.json').read_text())
                    validate_reference_fps(runtime, run_spec['fps'])
                    entry['phase']='official_full_evaluation'
                    states[key]=entry
                    atomic_json(output/'scheduler.json',{'updated_utc':utc_now(),'pid':os.getpid(),'cohorts':states})
                    videos = exported_videos(config,model,track,cohort/'generated_videos_5s')
                    result = cohort/'official_results'
                    command=[str(runtime['python']),'-u','-m','physiq.run_physics_iq',
                             '--input_folders',str(videos),'--output_folder',str(result),
                             '--descriptions_file',str(runtime['descriptions']),
                             '--benchmark_base_folder',str(runtime['benchmark_parent']),'--n_process',str(n_process)]
                    env=os.environ.copy()
                    env.update(PYTHONPATH=str(runtime['source']),PATH=str(runtime['bin_dir'])+os.pathsep+env.get('PATH',''),
                               MPLBACKEND='Agg',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2')
                    atomic_json(cohort/'evaluator_runtime.json', {**probe_runtime(runtime),
                        'upstream_commit':PHYSICS_IQ_COMMIT, 'dataset_revision':PHYSICS_IQ_DATASET_REVISION,
                        'started_utc':utc_now(), 'command':command})
                    with (cohort/'official_evaluation.log').open('ab') as log:
                        log.write(('\nEvaluation start '+utc_now()+'\n').encode())
                        code=subprocess.call(command,cwd=runtime['source'],env=env,stdout=log,stderr=subprocess.STDOUT)
                    if code:raise RuntimeError(f'Original evaluator exited {code}; log retained')
                    metrics_path=result/'physics-IQ-benchmark-verified/results'/f'{videos.name}_metrics.json'
                    metrics=json.loads(metrics_path.read_text())
                    submission=json.loads((cohort/'generated_videos_5s/input_manifest.json').read_text())
                    score={'protocol':'Physics-IQ Verified official','inputs':198,'independent_runs':1,
                           'model_key':model.key,'track':track,'input_mode':'I2V','prompt_setting':'bpp-base',
                           'fps':submission['fps'],'frames':submission['frames'],
                           'duration_seconds':submission['duration_seconds'],'resolution':submission['resolution'],
                           'upstream_commit':PHYSICS_IQ_COMMIT,'dataset_revision':PHYSICS_IQ_DATASET_REVISION,
                           'verified_score':metrics['final_score_view'],'original_formula_score':metrics['final_score_orig'],
                           'official_metrics_sha256':sha256(metrics_path),'completed_utc':utc_now(),
                           'organizer_published':False,'no_missing_output_imputation':True}
                    if any(not math.isfinite(float(score[k])) for k in ['verified_score','original_formula_score']):
                        raise ValueError('Original evaluator final score is not finite')
                    atomic_json(score_path,score)
                entry.update(phase='complete',official_full_score=True,score=score,result_dir=str(cohort))
            except Exception as exc:
                entry.update(phase='failed',error_type=type(exc).__name__,error=str(exc))
            states[key]=entry
    atomic_json(output/'scheduler.json',{'updated_utc':utc_now(),'pid':os.getpid(),'cohorts':states})
    return states


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    paths=parser.add_mutually_exclusive_group(required=True)
    paths.add_argument('--resource-root',type=Path,help='Existing original legacy runtime/resource directory')
    paths.add_argument('--evaluator-ready',type=Path,help='PIXELWORLD_EVALUATOR_READY.json created by repository setup')
    parser.add_argument('--evaluator-python',type=Path,help='Existing dependency-checked interpreter; preserves official source pins')
    parser.add_argument('--retry-failed-cohort',action='append',default=[],help='Explicit model/track evaluation retry; preserves prior logs and exports')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--interval',type=float,default=60)
    parser.add_argument('--max-hours',type=float,default=24*14)
    parser.add_argument('--n-process',type=int,default=4)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args();config=load_config(args.config);output=args.output.resolve()
    if output.is_relative_to(config.runs_root.resolve()) or args.interval<1 or args.max_hours<=0 or args.n_process<1:
        raise ValueError('External evaluator directory and positive bounded settings required')
    output.mkdir(parents=True,exist_ok=True)
    resource=args.resource_root.resolve() if args.resource_root else None
    ready=args.evaluator_ready.resolve() if args.evaluator_ready else None
    runtime=validate_runtime(resource,ready,args.evaluator_python)
    probe_runtime(runtime)
    if any(runtime[k].is_relative_to(config.runs_root.resolve()) for k in ('source','benchmark_parent')):
        raise ValueError('Evaluator resources must remain outside generation workspaces')
    lock=(output/'scheduler.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    path=output/'scheduler.json';states=json.loads(path.read_text())['cohorts'] if path.exists() else {}
    if args.retry_failed_cohort:
        atomic_json(output/('scheduler_before_retry_'+str(time.time_ns())+'.json'),states)
        for key in args.retry_failed_cohort:
            if key not in states or states[key].get('phase') != 'failed':
                raise ValueError('Explicit retry requires an existing failed evaluator cohort: '+key)
            states[key]={**states[key], 'phase':'waiting_for_generation', 'previous_error':states[key].get('error'),
                         'retry_requested_utc':utc_now()}
    deadline=time.monotonic()+args.max_hours*3600
    while time.monotonic()<deadline and not (output/'STOP').exists():
        states=step(config,resource,output,states,args.n_process,ready,args.evaluator_python)
        if args.once or all(item['phase'] in ('complete','failed','incomplete_official_scope') for item in states.values()):break
        time.sleep(args.interval)


if __name__=='__main__':main()
