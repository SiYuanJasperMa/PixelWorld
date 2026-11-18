"""Prepare provided shared-50 baseline MP4s for unchanged RoboJudge inference."""
import argparse
import json
import os
from pathlib import Path

from tools.evaluate_robojudge_shared50 import (
    CODE_REVISION, CHECKPOINT_REVISION, DATASET_REVISION,
    HUMAN_DATASETS, HUMAN_PREFIX, ROBOT_PREFIX, load_tasks, sha256, scope_report,
)


def prepare(package: Path, output: Path):
    package, output = package.resolve(), output.resolve()
    tasks = load_tasks(package / 'shared50_tasks.json')
    protocol = json.loads((package / 'protocol.json').read_text())
    if protocol.get('cases') != 50 or protocol.get('task_type') != 'i2v':
        raise ValueError('Requires the complete shared-50 I2V package')
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index, task in enumerate(tasks):
        case = f'common50_{index:04d}'
        video = package / 'videos' / (case + '.mp4')
        initial = package / 'first_frames' / (case + '.png')
        prompt = (package / 'prompts' / (case + '.txt')).read_text().strip()
        prefix = HUMAN_PREFIX if task['dataset'] in HUMAN_DATASETS else ROBOT_PREFIX
        if prompt != prefix + task['instruction'].strip():
            raise ValueError(f'Prompt differs from pinned generation instruction: {case}')
        if not video.is_file() or not initial.is_file():
            raise FileNotFoundError(case)
        records.append({'case_id':case, 'status':'prepared', 'source_key':task['key'],
            'source_type':'provided_external_baseline_video',
            'export':{'sha256':sha256(video)}, 'initial_sha256':sha256(initial),
            'input':{'item_id':case, 'video_url':os.path.relpath(video, output),
                     'instruction':task['instruction'].strip(), 'init_frame_url':os.path.relpath(initial, output)}})
    scope = scope_report(records, [f'common50_{i:04d}' for i in range(50)])
    assert scope['complete_shared50']
    plan = {'schema':'robojudge-external-baseline-v1', 'model':'seedance25', 'track':'i2v',
        'code_revision':CODE_REVISION, 'checkpoint_revision':CHECKPOINT_REVISION,
        'dataset_revision':DATASET_REVISION, 'tasks_sha256':sha256(package / 'shared50_tasks.json'),
        'scope':scope, 'records':records,
        'attempt_selection_policy':'provided v1 baseline videos; no quality selection'}
    (output / 'plan.json').write_text(json.dumps(plan, indent=2))
    (output / 'inputs.jsonl').write_text(''.join(json.dumps(row['input']) + '\n' for row in records))
    return scope


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.package, args.output), indent=2))
