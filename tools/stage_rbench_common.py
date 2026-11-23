"""Prepare case-only inputs for the pinned official RBench Common Manipulation split."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile

from PIL import Image

from pixelworld.media import contract
from pixelworld.util import atomic_json, sha256

DATASET = 'DAGroup-PKU/RBench'
REVISION = '6bdccf349ff5a8f68302428351e94f34ecd62450'
SUBSET = 'common_manipulation'
CASE = re.compile(r'common_manipulation_(\d{4})$')
PROMPTS = 'prompts/common_manipulation_prompts.json'


def stage(source: Path, out: Path) -> dict:
    source, out = source.resolve(), out.absolute()
    if out.exists() or out.is_symlink() or out.is_relative_to(source):
        raise ValueError('Use a fresh independent input directory')
    evidence = json.loads((source/'upstream_manifest.json').read_text())
    if (evidence.get('dataset'), evidence.get('revision'), evidence.get('subset')) != (DATASET, REVISION, SUBSET):
        raise ValueError('Unexpected RBench dataset identity')
    checksums = {row['relative']: row['sha256'] for row in evidence['files']}
    meta = source/PROMPTS
    if meta.resolve() != meta or sha256(meta) != checksums.get(PROMPTS):
        raise ValueError('Official prompt hash differs from download manifest')
    rows = json.loads(meta.read_text())
    if not isinstance(rows, list) or len(rows) != 50:
        raise ValueError('Expected exactly 50 official Common Manipulation prompts')
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.rbench-inputs-', dir=out.parent))
    selected = []
    try:
        for i, record in enumerate(rows, start=1):
            name, prompt = record['name'], record['prompt']
            match = CASE.fullmatch(name)
            if not match or int(match.group(1)) != i or not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f'Invalid official prompt at position {i}')
            relative = f'imgs/{SUBSET}/{name}.jpg'
            if record['image_path'] != relative.removeprefix('imgs/'):
                raise ValueError(f'Unexpected official image path for {name}')
            pic = source/relative
            if pic.resolve() != pic or sha256(pic) != checksums.get(relative):
                raise ValueError(f'Official image hash mismatch: {name}')
            folder = staging/f'{i:04d}'
            folder.mkdir()
            with Image.open(pic) as image:
                rgb = image.convert('RGB')
                rgb.save(folder/'first_frame.png')
                scale = 720/min(rgb.size)
                width, height = (max(2, 2*round(n*scale/2)) for n in rgb.size)
            (folder/'prompt.txt').write_text(prompt, encoding='utf-8')
            atomic_json(folder/'task_spec.json', {
                'task_type':'i2v', 'benchmark':'rbench_common_manipulation_i2v_v1',
                'width':width, 'height':height, 'frames':120, 'fps':24, 'audio':None,
                'conditioning_resize_filter':'lanczos',
            })
            contract(folder)
            selected.append({'case':folder.name,'official_name':name,'official_image_sha256':checksums[relative],
                             'input_image_sha256':sha256(folder/'first_frame.png'),'prompt_sha256':sha256(folder/'prompt.txt')})
        manifest = {'dataset':DATASET,'revision':REVISION,'subset':SUBSET,'cases':selected,
                    'generation_candidates_per_case':1,'fps':24,'frames':120,'short_edge_pixels':720,
                    'evaluation_annotations_excluded_from_agent_cases':True}
        atomic_json(staging/'input_manifest.json',manifest)
        staging.rename(out)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(stage(args.source,args.output),indent=2))
