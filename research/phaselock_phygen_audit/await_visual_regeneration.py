"""Generate extra candidates for visually rejected cases after GPU workers finish."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'runs/phygen_i2v_qwen21_20261111'


def write(phase, **fields):
    temp = RUN / 'logs/regeneration_controller.tmp'
    temp.write_text(json.dumps({'phase': phase, 'pid': os.getpid(),
                                'updated_at': time.time(), **fields}, indent=2))
    temp.replace(RUN / 'logs/regeneration_controller.json')


def main():
    lock = (RUN / 'logs/regeneration_controller.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    config = json.loads((RUN / 'logs/regeneration_config.json').read_text())
    while True:
        status = json.loads((RUN / 'logs/full_controller.json').read_text())
        if status.get('phase') != 'generating':
            break
        write('waiting_for_full_gpu_workers', cases=config['indices'])
        time.sleep(30)
    config = json.loads((RUN / 'logs/regeneration_config.json').read_text())
    write('generating_additional_candidates', cases=config['indices'])
    command = [sys.executable, '-u', str(ROOT / 'research/phaselock_phygen_audit/run_qwen_gpu_lease.py'),
               '--gpu', str(config['gpu']), '--mode', 'smoke',
               '--indices', ','.join(map(str, config['indices']))]
    code = subprocess.call(command, cwd=ROOT)
    write('awaiting_actual_visual_review' if code == 0 else 'failed',
          returncode=code, cases=config['indices'],
          additional_candidates_visually_reviewed=False)


if __name__ == '__main__':
    main()
