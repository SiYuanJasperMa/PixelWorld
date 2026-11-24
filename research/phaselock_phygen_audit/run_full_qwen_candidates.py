"""Run first-frame candidates on six GPUs; retain the other two for evaluators."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'runs/phygen_i2v_qwen21_20261111'
GPUS = [0, 3, 4, 5, 6, 7]


def write(value):
    value.update(pid=os.getpid(), updated_at=time.time())
    temporary = RUN / 'logs/full_controller.tmp'
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(RUN / 'logs/full_controller.json')


def launch(gpu, shard, attempt):
    path = RUN / 'logs' / f'full_gpu_{gpu}_attempt_{attempt:02d}.log'
    command = [sys.executable, '-u', str(ROOT / 'research/phaselock_phygen_audit/run_qwen_gpu_lease.py'),
               '--gpu', str(gpu), '--mode', 'full', '--shard', str(shard), '--shards', str(len(GPUS))]
    with path.open('ab') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {'gpu': gpu, 'shard': shard, 'attempt': attempt, 'process': process, 'log': str(path)}


def main():
    assert (RUN / 'logs/FULL_CANDIDATE_GENERATION_APPROVED.json').is_file()
    lock = (RUN / 'logs/full_controller.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    jobs = [launch(gpu, shard, 1) for shard, gpu in enumerate(GPUS)]
    while True:
        rows = []
        active = False
        for index, job in enumerate(jobs):
            code = job['process'].poll()
            if code is not None and code != 0 and job['attempt'] < 3:
                jobs[index] = job = launch(job['gpu'], job['shard'], job['attempt'] + 1)
                code = None
            active |= code is None
            rows.append({k: v for k, v in job.items() if k != 'process'}
                        | {'pid': job['process'].pid, 'returncode': code})
        count = len(list((RUN / 'candidates').glob('*/*.png')))
        write({'phase': 'generating' if active else ('awaiting_actual_visual_selection' if count == 480 else 'incomplete'),
               'gpu_workers': rows, 'candidate_pngs': count, 'expected_candidates': 480,
               'selected_frames': len(list((RUN / 'selected').glob('*/first_frame.png'))),
               'video_generation_launched': False})
        if not active:
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
