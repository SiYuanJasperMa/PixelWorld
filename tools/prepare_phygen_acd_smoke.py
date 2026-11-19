"""Freeze a stratified, outcome-independent PhyGenBench A/C/D smoke.

Qwen is used solely to prepare analysis initialization images. The downstream
coding-agent contract remains the unmodified PixelWorld I2V contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


PREACTION = (
    'Create one static frame showing the scene immediately BEFORE the event below begins. '
    'Include the objects, materials and environment needed for the event, in their initial state. '
    'Do not depict the event already happening or its outcome. '
    'Make the entire scene as photorealistic as possible. '
    'No text, labels, collage or multiple panels.\n\nEvent: {caption}\n'
)
RESTYLE = (
    'Re-render the supplied image as one photorealistic live-action photograph. '
    'Preserve its composition, objects, positions and pre-action scene state. '
    'Improve photographic materials, textures and lighting. Do not advance the action '
    'or depict its outcome. No text, labels, collage or multiple panels. '
    'The later video event is: {caption}'
)
NEGATIVE = 'CGI, 3D render, cartoon, illustration, text, labels, collage, multiple panels'


def prepare(release: Path, seed: int = 20261119):
    repo = release.parent.parent
    root = release / 'analysis' / 'phygen_acd_smoke10'
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        return manifest_path
    source = repo / 'vendor/phygenbench-official-source/PhyGenBench/prompts.json'
    rows = json.loads(source.read_text())
    bins = {'Mechanics': [], 'Optics': [], 'Thermal': [], 'Material': []}
    mapping = {'Force': 'Mechanics', 'Light': 'Optics', 'Heat': 'Thermal',
               'Physical Properties': 'Material', 'Chemical Properties': 'Material'}
    for i, row in enumerate(rows, 1):
        bins[mapping[row['main_category']]].append((i, row))
    rng = random.Random(seed)
    cases = []
    for domain, count in [('Mechanics', 3), ('Optics', 3), ('Thermal', 2), ('Material', 2)]:
        for index, row in sorted(rng.sample(bins[domain], count)):
            cid = f'phygen_{index:03d}'
            cases.append({'id': cid, 'official_index': index, 'dimension': domain, **row,
                          'caption_sha256': hashlib.sha256(row['caption'].encode()).hexdigest()})
    manifest = {
        'protocol': 'pixelworld-phygen-acd-smoke10-v1', 'seed': seed,
        'selection': 'stratified random 3/3/2/2, fixed before new generation or scoring',
        'official_prompts_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'model': 'gpt61-sol', 'track': 'vision-agent', 'reasoning_effort': 'high',
        'width': 1280, 'height': 720, 'fps': 24, 'frames': 120,
        'routes': {'A': 'coding-agent T2V; reuse compatible existing main deliveries',
                   'C': 'coding-agent pixel still -> Qwen image-conditioned restyle -> coding-agent I2V',
                   'D': 'Qwen text-to-image pre-action still -> coding-agent I2V'},
        'qwen': {'model': 'Qwen-Image-2.1', 'native_size': [1536, 864],
                 'output_size': [1280, 720], 'steps': 40, 'cfg': 5,
                 'seeds': [0, 1, 2], 'selection': 'first acceptable seed in ascending order; initialization-only review, no downstream scores',
                 'negative_prompt': NEGATIVE, 'preaction_template': PREACTION,
                 'restyle_template': RESTYLE},
        'cases': cases,
    }
    main = json.loads((release / 'configs/phygenbench_full.json').read_text())
    model = next(m for m in main['models'] if m['key'] == 'gpt61-sol')
    model['tracks'] = ['vision-agent']
    input_root = root / 'pixel_inputs'
    for row in cases:
        case = input_root / row['id']
        case.mkdir(parents=True, exist_ok=True)
        (case / 'prompt.txt').write_text(PREACTION.format(caption=row['caption']))
        write(case / 'task_spec.json', {'task_type': 't2v', 'width': 1280, 'height': 720,
                                      'fps': 1.0, 'frames': 1, 'audio': None,
                                      'benchmark': 'phygenbench_firstframe', 'visual_target': 'photoreal'})
        write(case / 'preparation_record.json', {'official_index': row['official_index'],
                                               'original_caption': row['caption'], 'purpose': 'analysis pre-action still'})
    config = {
        'protocol': 'pixelworld-phygen-acd-pixelinit-smoke10-v1',
        'inputs_root': str(input_root),
        'runs_root': str(release / 'analysis_generations/phygen_acd_pixelinit'),
        'case_ids': [c['id'] for c in cases], 'models': [model],
        'max_concurrency': 3, 'max_per_config': 3, 'max_attempts': 1,
        'max_total_attempts': 5, 'output_token_budget': 1000000,
        'filesystem_policy': 'case-only-v1', 'harness_variant': 'baseline',
    }
    write(release / 'configs/phygen_acd_pixelinit_smoke.json', config)
    write(input_root.parent / 'pixel_inputs_manifest.json', {'cases': [{'id': c['id']} for c in cases]})
    write(manifest_path, manifest)
    return manifest_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261119)
    args = parser.parse_args()
    print(prepare(args.release.resolve(), args.seed))
