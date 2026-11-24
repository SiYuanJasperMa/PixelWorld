import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'runs' / 'phygen_i2v_qwen21_20261111'

def status(phase, **extra):
    path = ROOT / 'logs' / 'setup_status.json'
    path.write_text(json.dumps({'phase': phase, 'pid': os.getpid(), 'updated_at': time.time(), **extra}, indent=2) + '\n')

def main():
    config = json.loads((ROOT / 'protocol.json').read_text())
    temporary = ROOT/'runtime'/'tmp'
    temporary.mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(temporary)
    env = ROOT / 'runtime' / 'venv'
    if not (env / 'bin' / 'python').exists():
        status('creating_environment')
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(env)], check=True)
    # Reuse only CUDA Torch read-only, not unrelated packages from a training
    # environment (its optional TorchAudio may have a different CUDA build).
    packages = env / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    borrowed = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    for path in borrowed.iterdir():
        name=path.name.lower()
        reuse=(name in {'torch','torchgen','functorch','nvidia','cuda','triton','pip'}
               or name.startswith(('torch-', 'nvidia_', 'cuda_', 'triton-', 'pip-')))
        target=packages/path.name
        if reuse and not target.exists():
            target.symlink_to(path, target_is_directory=path.is_dir())
    (packages/'borrowed_cuda_environment.pth').unlink(missing_ok=True)
    python = str(env / 'bin' / 'python')
    status('installing_dependencies')
    local_source=ROOT/'runtime'/f"diffusers-{config['diffusers_revision']}"
    dependency_source=str(local_source) if local_source.is_dir() else f"git+https://github.com/huggingface/diffusers.git@{config['diffusers_revision']}"
    subprocess.run([
        python, '-m', 'pip', 'install', '--disable-pip-version-check',
        dependency_source,
        'transformers>=5.17', 'accelerate', 'pillow', 'safetensors',
        'huggingface_hub', 'sentencepiece', 'protobuf', 'ftfy',
    ], check=True)
    status('checking_pipeline')
    subprocess.run([python, '-c', 'from diffusers import QwenImage21Pipeline; import torch; print(QwenImage21Pipeline, torch.__version__)'], check=True)
    status('downloading_weights', model=config['initializer'], revision=config['initializer_revision'])
    code = (
        'from huggingface_hub import snapshot_download; '
        f"snapshot_download({config['initializer']!r}, revision={config['initializer_revision']!r}, "
        f"cache_dir={str(ROOT / 'runtime' / 'model_cache')!r}, "
        "ignore_patterns=['assets/*', '*.png', '*.jpg', '*.md'], max_workers=6)"
    )
    subprocess.run([python, '-c', code], check=True)
    subprocess.run([python, '-m', 'pip', 'freeze'], check=True, stdout=(ROOT / 'runtime' / 'requirements.lock.txt').open('w'))
    status('ready')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        status('failed', error=f'{type(exc).__name__}: {exc}')
        raise
