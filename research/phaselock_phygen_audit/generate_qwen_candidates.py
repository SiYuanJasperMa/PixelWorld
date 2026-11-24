"""GPU computation only. All artifacts reside in the shared PixelWorld run."""
import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path

def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--indices', default='')
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    config = json.loads((root / 'protocol.json').read_text())
    rows = json.loads((root / 'paired_prompts.json').read_text())
    indices = set(map(int, args.indices.split(','))) if args.indices else None
    rows = [r for r in rows if (r['source_index'] in indices if indices is not None else r['source_index'] % args.shards == args.shard)]
    status_path = root / 'logs' / f'worker_{args.shard}_status.json'
    started = time.time()
    atomic_json(status_path, {'phase': 'loading_model', 'pid': os.getpid(), 'started_at': started})
    import torch
    import diffusers
    import transformers
    from diffusers import QwenImage21Pipeline
    pipe = QwenImage21Pipeline.from_pretrained(
        config['initializer'], revision=config['initializer_revision'],
        torch_dtype=torch.bfloat16, cache_dir=str(root / 'runtime' / 'model_cache'),
        local_files_only=True,
    ).to('cuda')
    pipe.set_progress_bar_config(disable=True)
    atomic_json(root / 'logs' / f'worker_{args.shard}_environment.json', {
        'python': platform.python_version(), 'torch': torch.__version__,
        'diffusers': diffusers.__version__, 'transformers': transformers.__version__,
        'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(),
        'model_revision': config['initializer_revision'], 'configuration': config,
    })
    done = 0
    for row in rows:
        folder = root / 'candidates' / row['case_id']
        folder.mkdir(parents=True, exist_ok=True)
        for j, seed in enumerate(row['candidate_seeds']):
            target = folder / f'candidate_{j:02d}.png'
            receipt = target.with_suffix('.json')
            if target.exists() and receipt.exists():
                done += 1
                continue
            atomic_json(status_path, {'phase': 'generating', 'case_id': row['case_id'], 'candidate': j, 'done': done, 'started_at': started, 'updated_at': time.time()})
            tick = time.time()
            image = pipe(
                prompt=row['image_prompt'], width=config['width'], height=config['height'],
                num_inference_steps=config['steps'],
                generator=torch.Generator('cuda').manual_seed(seed),
            ).images[0].convert('RGB')
            temp = target.with_suffix('.tmp.png')
            image.save(temp)
            temp.replace(target)
            atomic_json(receipt, {
                'case_id': row['case_id'], 'candidate': j, 'seed': seed,
                'image_prompt': row['image_prompt'], 'video_prompt': row['video_prompt'],
                'model': config['initializer'], 'model_revision': config['initializer_revision'],
                'size': list(image.size), 'steps': config['steps'],
                'seconds': time.time() - tick, 'completed_at': time.time(),
                'image_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            })
            done += 1
            print(f"Saved {row['case_id']} candidate={j} seed={seed} seconds={time.time()-tick:.1f}", flush=True)
    atomic_json(status_path, {'phase': 'complete', 'done': done, 'started_at': started, 'completed_at': time.time()})

if __name__ == '__main__':
    main()
