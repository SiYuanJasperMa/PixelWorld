"""Download on the main workspace host; the SSH GPU worker stays offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'runs/phygen_i2v_qwen21_20261111'

def state(phase, **fields):
    path=RUN/'logs/setup_status.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'phase':phase,'pid':os.getpid(),'download_host':'main_workspace','updated_at':time.time(),**fields},indent=2))
    temporary.replace(path)

def main():
    from huggingface_hub import snapshot_download
    config=json.loads((RUN/'protocol.json').read_text())
    state('downloading_weights_locally',model=config['initializer'])
    folder=snapshot_download(config['initializer'],revision=config['initializer_revision'],
        cache_dir=str(RUN/'runtime/model_cache'),ignore_patterns=['assets/*','*.png','*.jpg','*.md'],max_workers=8)
    (RUN/'runtime/checkpoint_snapshot.txt').write_text(folder+'\n')
    with (RUN/'runtime/requirements.lock.txt').open('w') as log:
        subprocess.run([sys.executable,'-m','pip','freeze'],stdout=log,check=True)
    state('ready',model=config['initializer'],revision=config['initializer_revision'],snapshot=folder)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        state('failed',error=f'{type(exc).__name__}: {exc}')
        raise
