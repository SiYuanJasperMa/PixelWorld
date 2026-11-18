"""Run a complete PCVE cohort using cloud-resident dependencies and GPU claims."""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys

from pixelworld.util import atomic_json
from tools.live_evaluation_data import pcve_score, read


def environment(code, bundle, output):
    env = os.environ.copy()
    packages = [bundle / 'state/pcve_eval_packages',
                code / 'vendor/robojudge-eval-night/detached-runtime/lib/python3.11/site-packages',
                code.parent / 'volo_step9/sam2-site']
    env.update(PYTHONPATH=os.pathsep.join(map(str, [code, *packages])),
               HF_HOME=str(bundle / 'state/hf_cache'), HF_HUB_CACHE=str(bundle / 'state/hf_cache'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1')
    for key, path in [('TMPDIR', output / '.tmp'), ('TORCH_HOME', bundle / 'state/hf_cache/torch'),
                      ('MPLCONFIGDIR', bundle / 'state/matplotlib'),
                      ('XDG_CACHE_HOME', bundle / 'state/cache'),
                      ('CUDA_CACHE_PATH', bundle / 'state/cache/cuda'),
                      ('TORCHINDUCTOR_CACHE_DIR', bundle / 'state/cache/inductor'),
                      ('TRITON_CACHE_DIR', bundle / 'state/cache/triton'),
                      ('IMAGEIO_USERDIR', bundle / 'state/imageio')]:
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--track', choices=['text', 'vision-agent'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    args = parser.parse_args()
    code = Path(__file__).resolve().parents[1]
    bundle, output = args.bundle.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'cohort.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if pcve_score(read(output / 'summary.json'), args.model, args.track):
            return
        if not all((bundle / 'state' / name).is_file() for name in ('gdino_ready.json', 'clip_ready.json')):
            raise RuntimeError('Wait for verified shared evaluator weights before launch')
        env = environment(code, bundle, output)
        env['IMAGEIO_FFMPEG_EXE'] = str(args.ffmpeg.absolute())
        evaluator = [str(args.python.absolute()), '-u', '-m', 'tools.evaluate_pcve_full',
                     '--config', str(args.config.resolve()), '--model', args.model, '--track', args.track,
                     '--output', str(output), '--evaluator-python', str(args.python.absolute()),
                     '--evaluator-source', str(code / 'vendor/pcve-rigid-eval-pinned'), '--device', 'cuda']
        command = [str(args.python.absolute()), '-u', '-m', 'tools.run_gpu_evaluation',
                   '--state-root', str(bundle / 'state/gpu_claims'),
                   '--status', str(output / 'gpu_worker.json'), '--min-free-gib', '32', '--', *evaluator]
        import time
        state = {'phase': 'evaluation_running', 'pid': os.getpid(), 'updated_at': time.time(),
                 'config': str(args.config.resolve()), 'model': args.model, 'track': args.track}
        atomic_json(output / 'worker_state.json', state)
        status = subprocess.call(command, cwd=code, env=env)
        state.update(phase='finished' if status == 0 else 'failed', returncode=status, updated_at=time.time())
        atomic_json(output / 'worker_state.json', state)
        raise SystemExit(status)


if __name__ == '__main__':
    main()
