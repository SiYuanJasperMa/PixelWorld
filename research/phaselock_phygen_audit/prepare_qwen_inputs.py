"""Prepare caption-only paired inputs; do not import physics answers into tasks."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent.parent / 'runs' / 'phygen_i2v_qwen21_20261111'
SUFFIX = (
    ', static scene immediately before the action. '
    'A single photorealistic live-action camera frame with realistic materials and lighting. '
    'Show the initial state, before the described change or movement begins, '
    'with all required objects visible and placed for the action to occur. '
    'For a scene that is already static, depict that scene as described. '
    'No diagrams, captions, labels, collage, or before-and-after panels.'
)

def main():
    raw = (ROOT / 'official_phygen_prompts.json').read_bytes()
    source = json.loads(raw)
    assert len(source) == 160
    rows = []
    for index, item in enumerate(source):
        case_id = f'phygen_{index + 1:03d}'
        caption = item['caption']
        folder = OUT / 'inputs' / case_id
        folder.mkdir(parents=True, exist_ok=True)
        image_prompt = caption + SUFFIX
        (folder / 'prompt.txt').write_text(caption)
        (folder / 'image_prompt.txt').write_text(image_prompt)
        rows.append({
            'case_id': case_id, 'source_index': index,
            'video_prompt': caption, 'image_prompt': image_prompt,
            'video_prompt_sha256': hashlib.sha256(caption.encode()).hexdigest(),
            'candidate_seeds': [20261111 + index * 100 + j for j in range(3)],
        })
    (OUT / 'paired_prompts.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    config = {
        'protocol': 'phygen_i2v_qwen21_v1',
        'source_repo': 'OpenGVLab/PhyGenBench',
        'source_revision': 'f8642cb796f3bcb01f0b7c1b2ec53b75d357c739',
        'source_prompts_sha256': hashlib.sha256(raw).hexdigest(),
        'initializer': 'Qwen/Qwen-Image-2.1',
        'initializer_revision': 'd26bb61231c349cf6b7896fa83353113880e1ba3',
        'diffusers_revision': '0905b667fe3f37f43b6ef9c6e05ad20636206938',
        'width': 1024, 'height': 576, 'steps': 40, 'dtype': 'bfloat16',
        'candidates_per_case': 3, 'smoke_indices': [0, 60, 90, 120],
        'phase_lock_published_suffix': ', static scene immediately before the action.',
        'ours_additional_instruction': SUFFIX.split('action.', 1)[1].strip(),
        'selection': 'Visual inspection of initial-state correctness, scene and object match, framing, and photographic quality. No reference video or physics answer is used.',
        'status': 'prepared; no videos launched',
    }
    (OUT / 'protocol.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    for row, item in zip(rows, source):
        assert row['video_prompt'] == item['caption']
        assert set(row).isdisjoint({'physical_laws', 'answer', 'reference_video'})
    print(f'Prepared {len(rows)} paired caption-only tasks in {OUT}')

if __name__ == '__main__':
    main()
