"""Prepare C/D initial images for a frozen analysis; never generate video frames."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from tools.prepare_phygen_acd_smoke import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    release = args.release.resolve()
    root = release / 'analysis/phygen_acd_smoke10'
    manifest = json.loads((root / 'manifest.json').read_text())
    repo = release.parent.parent
    checkpoint = Path((repo / 'runs/phygen_i2v_qwen21_20261111/runtime/checkpoint_snapshot.txt').read_text().strip())
    import torch
    from PIL import Image
    from diffusers import QwenImage21Pipeline
    from pixelworld.config import load_config
    from pixelworld.supervisor import inspect
    from tools.remote_case_status import remote_case_status

    state = root / 'qwen_state.json'
    write(state, {'phase': 'loading', 'pid': os.getpid(), 'host': os.uname().nodename,
                  'checkpoint_revision': checkpoint.name, 'updated_unix': time.time()})
    pipe = QwenImage21Pipeline.from_pretrained(str(checkpoint), torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_slicing()
    pipe.vae.enable_tiling()
    cfg = load_config(release / 'configs/phygen_acd_pixelinit_smoke.json')
    model = cfg.model('gpt61-sol')
    qwen = manifest['qwen']
    torch.set_num_threads(8)
    while not (root / 'QWEN_STOP').exists():
        waiting = []
        # Alternate the two routes so C can start while later D scenes render.
        for route, index in [(route, i) for i in range(len(manifest['cases'])) for route in ['D', 'C']]:
            for row in manifest['cases'][index:index+1]:
                cid = row['id']
                output = root / 'qwen' / route / cid
                output.mkdir(parents=True, exist_ok=True)
                if all((output / f'seed{s}.render.json').exists() for s in qwen['seeds']):
                    continue
                source = None
                lineage = {}
                if route == 'C':
                    result = remote_case_status(cfg, model, 'vision-agent', cid, inspect(cfg, model, 'vision-agent', cid))
                    if result['state'] != 'complete':
                        waiting.append({'route': route, 'case': cid, 'pixel_state': result['state']})
                        continue
                    run = Path(result['run'])
                    frames = sorted((run / 'workspace/frames').glob('*.png'))
                    if len(frames) != 1:
                        raise ValueError(f'Expected one accepted pixel frame for {cid}')
                    source_path = frames[0]
                    source = Image.open(source_path).convert('RGB')
                    lineage = {'pixel_run': str(run), 'source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()}
                    # The original pixel initialization stays independently visible.
                    source.save(output / 'agent_pixel_init.png')
                prompt = (qwen['restyle_template'] if route == 'C' else qwen['preaction_template']).format(caption=row['caption'])
                for seed in qwen['seeds']:
                    target = output / f'seed{seed}.png'
                    metadata = output / f'seed{seed}.render.json'
                    if metadata.exists():
                        if hashlib.sha256(target.read_bytes()).hexdigest() != json.loads(metadata.read_text())['output_sha256']:
                            raise ValueError(f'Frozen Qwen candidate changed: {target}')
                        continue
                    start = time.time()
                    write(state, {'phase': 'rendering', 'route': route, 'case': cid, 'seed': seed,
                                  'pid': os.getpid(), 'updated_unix': start})
                    native = pipe(prompt=prompt, image=source, negative_prompt=qwen['negative_prompt'],
                                  true_cfg_scale=qwen['cfg'], height=qwen['native_size'][1], width=qwen['native_size'][0],
                                  output_resolution=max(qwen['native_size']), num_inference_steps=qwen['steps'],
                                  generator=torch.Generator(device='cuda').manual_seed(seed)).images[0]
                    # Native 16:9 canvas -> equal-aspect rescale, no stretching.
                    if native.width * qwen['output_size'][1] != native.height * qwen['output_size'][0]:
                        raise ValueError(f'Unexpected native aspect ratio: {native.size}')
                    native.save(output / f'seed{seed}_native.png')
                    selected = native.convert('RGB').resize(tuple(qwen['output_size']), Image.Resampling.LANCZOS)
                    tmp = target.with_suffix('.tmp.png'); selected.save(tmp); tmp.replace(target)
                    write(metadata, {'route': route, 'case': cid, 'seed': seed, 'prompt': prompt,
                                     'negative_prompt': qwen['negative_prompt'], 'steps': qwen['steps'], 'cfg': qwen['cfg'],
                                     'pipeline': type(pipe).__name__, 'checkpoint_revision': checkpoint.name,
                                     'native_size': list(native.size), 'output_size': list(selected.size),
                                     'output_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                                     'elapsed_seconds': time.time() - start, 'scope': 'initial image only', **lineage})
                    print(json.dumps({'route': route, 'case': cid, 'seed': seed, 'seconds': round(time.time()-start)}), flush=True)
                    torch.cuda.empty_cache()
        write(state, {'phase': 'waiting_pixel_initialization' if waiting else 'complete', 'waiting': waiting,
                      'pid': os.getpid(), 'host': os.uname().nodename, 'updated_unix': time.time()})
        if not waiting:
            (root / 'QWEN_FINISHED').write_text('All frozen C/D initial candidates prepared.\n')
            return
        # Exhausted C initializations are recorded as route failures, not rerendered.
        if all(x['pixel_state'] == 'exhausted' for x in waiting):
            write(state, {'phase': 'complete_with_pixel_failures', 'failures': waiting, 'updated_unix': time.time()})
            (root / 'QWEN_FINISHED').write_text('Completed with recorded pixel-initialization failures.\n')
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
