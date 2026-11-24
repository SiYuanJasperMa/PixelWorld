#!/usr/bin/env python3
"""Evaluate preserved video baselines with pinned official per-view functions.

Explicit partial coverage is retained; no full198 score, generation or imputation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixelworld.constants import (PHYSICS_IQ_COMMIT, PHYSICS_IQ_DATASET_REVISION,
                                  PHYSICS_IQ_DESCRIPTIONS_SHA256)
from pixelworld.util import atomic_json, sha256, utc_now


def official_cases() -> list[dict]:
    record = json.loads((ROOT / 'data/physics_iq_verified_i2v.json').read_text())
    if record['dataset_revision'] != PHYSICS_IQ_DATASET_REVISION:
        raise ValueError('Pinned identity manifest revision mismatch')
    cases = record['cases']
    if [c['id'] for c in cases] != [f'{i:04d}' for i in range(1, 199)]:
        raise ValueError('Pinned take1 identities incomplete')
    return cases


def validate_runtime(resource: Path, python_override: Path | None = None) -> dict:
    source = resource / 'upstream/physics-IQ-benchmark'
    descriptions = resource / 'protocol/descriptions_base.csv'
    python = (python_override or resource / '.venv/bin/python').absolute()
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'diff', 'HEAD', '--name-only'], text=True).strip()
    revision = json.loads((resource / 'protocol/dataset.json').read_text())['revision']
    if (commit != PHYSICS_IQ_COMMIT or dirty or revision != PHYSICS_IQ_DATASET_REVISION
            or sha256(descriptions) != PHYSICS_IQ_DESCRIPTIONS_SHA256):
        raise ValueError('Official evaluator source/dataset/descriptions pin mismatch')
    if Path(sys.executable).absolute() != python:
        raise ValueError('Run this adapter with the selected evaluator Python')
    return {'source': source, 'descriptions': descriptions, 'python': python,
            'benchmark_parent': resource / 'data'}


def probe_runtime(runtime: dict) -> dict:
    sys.path.insert(0, str(runtime['source']))
    import cv2, numpy, pandas, scipy, matplotlib
    import physiq.run_physics_iq as evaluator
    if not Path(evaluator.__file__).resolve().is_relative_to(runtime['source'].resolve()):
        raise ValueError('Imported unrelated evaluator')
    return {'python': sys.executable, 'evaluator': evaluator.__file__,
            **{name: module.__version__ for name, module in
               [('cv2', cv2), ('numpy', numpy), ('pandas', pandas), ('scipy', scipy), ('matplotlib', matplotlib)]}}


def per_view_score(metrics: dict, view: str) -> dict:
    """Use official normalization methods on exactly one observed view.

    IQTable's full constructor requires three views. Here its per-view methods
    consume the same parsed/rounded metric columns for the available view only.
    No absent perspective is created or assigned a value.
    """
    import pandas as pd
    from physiq.calculate_iq_score import parse_list_of_floats
    from physiq.calculate_iq_score_stable import IQTable, clip
    table = IQTable.__new__(IQTable)
    table.views = [view]
    table.df = pd.DataFrame([metrics])
    for key in IQTable.get_list_keys():
        values = parse_list_of_floats(metrics[f'{key}_{view}'])
        table.df[f'{key}_mean_{view}'] = sum(values) / len(values)
    row = clip(table.compute_scores_scenario_by_view()).iloc[0]
    return {key: float(value) for key, value in row.items()}


def score_one(job: dict) -> dict:
    sys.path.insert(0, job['upstream'])
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    from physiq.binary_mask_generator import generate_mask
    from physiq.calculate_and_write_metrics_to_csv import ViewPaths, process_view
    video = Path(job['video'])
    identifier, view, scenario = video.name.split('_', 2)
    cap = cv2.VideoCapture(str(video))
    fps, frames = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    last_ok = False
    if cap.isOpened() and frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frames - 1)
        last_ok = cap.read()[0]
    cap.release()
    if fps != 24 or frames != 120 or not last_ok:
        raise ValueError(f'Expected decodable native24FPS/120-frame video: {video}')
    if sha256(video) != job['video_sha256']:
        raise ValueError('Preserved video changed since inventory')
    dataset = Path(job['dataset'])
    real = dataset / 'split-videos/testing/24FPS'
    masks = dataset / 'video-masks/real/24FPS'
    take1 = real / job['take1']
    take2 = real / job['take2']
    mask1 = masks / job['take1'].replace('testing-videos', 'video-masks')
    mask2 = masks / job['take2'].replace('testing-videos', 'video-masks')
    for path in (take1, take2, mask1, mask2):
        if not path.is_file():
            raise FileNotFoundError(path)
    directory = Path(job['mask_dir']) / identifier
    directory.mkdir(parents=True, exist_ok=True)
    generated_mask = directory / f'{identifier}_video-masks_24FPS_{view}_take-1_{scenario}'
    generate_mask(str(video), str(directory / video.name), False)
    maskcap = cv2.VideoCapture(str(generated_mask))
    valid = maskcap.isOpened() and int(maskcap.get(cv2.CAP_PROP_FRAME_COUNT)) == 120
    maskcap.release()
    if not valid:
        raise ValueError('Official generated mask is incomplete')
    metrics = process_view(ViewPaths(str(take1), str(take2), str(video),
                                    str(mask1), str(mask2), str(generated_mask)), view, 0, 120, 120)
    if metrics is None:
        raise ValueError('Official process_view returned no metrics')
    normalized = per_view_score(metrics, view)
    if not np.isfinite(list(normalized.values())).all():
        raise ValueError('Official per-view score is nonfinite')
    return {**job, 'metrics': metrics, 'official_normalized_per_view': normalized,
            'media': {'fps': fps, 'frames': frames, 'width': width, 'height': height},
            'reference_sha256': {p.name: sha256(p) for p in (take1, take2, mask1, mask2)},
            'generated_mask_sha256': sha256(generated_mask)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video-dir', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--cohort', required=True)
    parser.add_argument('--resource-root', type=Path, required=True)
    parser.add_argument('--evaluator-python', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--wait-for-memory', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    lock = (output / 'evaluation.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runtime = validate_runtime(args.resource_root.resolve(), python_override=args.evaluator_python)
    dependency = probe_runtime(runtime)
    sys.path.insert(0, str(runtime['source']))
    from tools.evaluation_memory import check_pool_memory
    cases = official_cases()
    with runtime['descriptions'].open() as handle:
        descriptions = list(csv.DictReader(handle))
    real_names = {r['scenario']: r for r in descriptions}
    dataset = runtime['benchmark_parent'] / 'physics-IQ-benchmark-verified'
    sources = {p.name: p.resolve() for p in args.video_dir.glob('*.mp4') if p.is_file()}
    expected = {c['generated_video_name'] for c in cases}
    if set(sources) - expected:
        raise ValueError('Videos outside the pinned take1 identity set')
    jobs = []
    for case in cases:
        if case['generated_video_name'] not in sources or (args.case and case['id'] not in args.case):
            continue
        identifier, view, scenario = case['generated_video_name'].split('_', 2)
        take1key = f'{identifier}_{view}_take-1_{scenario}'
        take2key = next(k for k in real_names if k.endswith(f'_{view}_take-2_{scenario}'))
        jobs.append({'case_id': identifier, 'view': view, 'scenario': scenario,
                     'video': str(args.video_dir.resolve() / case['generated_video_name']),
                     'source_video': str(sources[case['generated_video_name']]),
                     'video_sha256': sha256(sources[case['generated_video_name']]),
                     'upstream': str(runtime['source']), 'dataset': str(dataset),
                     'mask_dir': str(output / 'generated_masks'),
                     'take1': real_names[take1key]['scenario'].replace('_', '_testing-videos_24FPS_', 1),
                     'take2': real_names[take2key]['scenario'].replace('_', '_testing-videos_24FPS_', 1)})
    if not jobs:
        raise ValueError('No pinned take1 videos selected')
    missing = [c['id'] for c in cases if c['id'] not in {j['case_id'] for j in jobs}]
    base = {'protocol': 'Physics-IQ Verified pinned official per-view subset',
            'cohort': args.cohort, 'upstream_commit': PHYSICS_IQ_COMMIT,
            'dataset_revision': PHYSICS_IQ_DATASET_REVISION,
            'official_full_score': False, 'organizer_published': False,
            'no_missing_output_imputation': True, 'expected_cases': 198,
            'available_cases': len(jobs), 'missing_case_ids': missing,
            'source_provenance': str(args.provenance.resolve()),
            'source_provenance_sha256': sha256(args.provenance),
            'started_utc': utc_now(), 'command': [sys.executable, *sys.argv],
            'runtime': dependency, 'aggregation': 'mean of official normalized scores over observed views only'}
    atomic_json(output / 'input_inventory.json', {**base, 'videos': jobs})
    checkpoint = output / 'checkpoint.json'
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if saved and any(saved.get(key) != base.get(key) for key in
                     ('upstream_commit', 'dataset_revision', 'cohort', 'source_provenance_sha256')):
        raise ValueError('Existing metrics belong to a different pinned evaluation/provenance')
    previous = saved.get('per_case', [])
    accepted = {r['case_id']: r for r in previous}
    if set(accepted) - {j['case_id'] for j in jobs}:
        raise ValueError('Existing metrics contain views outside the selected subset')
    for job in jobs:
        if job['case_id'] in accepted and accepted[job['case_id']]['video_sha256'] != job['video_sha256']:
            raise ValueError('Previously scored source changed')
    pending = [j for j in jobs if j['case_id'] not in accepted]
    deadline = time.monotonic() + 24 * 3600
    while pending:
        try:
            check_pool_memory(args.workers, 120)
            break
        except MemoryError as error:
            atomic_json(output / 'execution.json', {**base, 'status': 'waiting_for_memory',
                                                   'pid': os.getpid(), 'completed_cases': len(accepted),
                                                   'updated_utc': utc_now(), 'memory_error': str(error)})
            if not args.wait_for_memory or time.monotonic() >= deadline:
                raise
            print(str(error), flush=True)
            time.sleep(30)
    atomic_json(output / 'execution.json', {**base, 'status': 'running', 'pid': os.getpid(), 'completed_cases': len(accepted)})
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(score_one, job): job for job in pending}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                for pending_future in futures:
                    pending_future.cancel()
                atomic_json(output / 'execution.json', {**base, 'status': 'failed', 'pid': os.getpid(),
                            'completed_cases': len(accepted), 'failed_case_ids': [futures[future]['case_id']],
                            'error_type': type(error).__name__, 'error': str(error)})
                raise
            accepted[result['case_id']] = result
            rows = [accepted[k] for k in sorted(accepted)]
            atomic_json(checkpoint, {**base, 'updated_utc': utc_now(), 'per_case': rows})
            atomic_json(output / 'execution.json', {**base, 'status': 'running', 'pid': os.getpid(), 'completed_cases': len(rows)})
            print('Scored', args.cohort, result['case_id'], len(rows), '/', len(jobs), flush=True)
    rows = [accepted[k] for k in sorted(accepted)]
    partial = sum(r['official_normalized_per_view']['final_arithmetic'] for r in rows) / len(rows)
    atomic_json(output / 'scores.json', {**base, 'status': 'complete', 'completed_utc': utc_now(),
                                        'completed_cases': len(rows), 'partial_verified_score_fraction': partial,
                                        'partial_verified_score_percent': 100 * partial})
    atomic_json(output / 'execution.json', {**base, 'status': 'complete', 'pid': os.getpid(), 'completed_cases': len(rows)})
    print(json.dumps({'cohort': args.cohort, 'partial_score_percent': partial * 100, 'coverage': f'{len(rows)}/198'}), flush=True)


if __name__ == '__main__':
    main()
