"""Run an evaluator on a GPU with enough free memory, holding a shared claim.

The claim coordinates PixelWorld jobs only. Other workloads are never stopped.
All state and locks are written under the caller's evaluation-side state root.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def free_gpus() -> list[tuple[int, int]]:
    result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.free,utilization.gpu',
                             '--format=csv,noheader,nounits'], check=True,
                            capture_output=True, text=True, timeout=30)
    rows = []
    for line in result.stdout.splitlines():
        index, memory, utilization = line.split(',')
        if utilization.strip().isdigit() and int(utilization.strip()) <= 20:
            rows.append((int(index.strip()), int(memory.strip())))
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def claim_gpu(root: Path, minimum_mib: int):
    for index, available in free_gpus():
        if available < minimum_mib:
            continue
        handle = (root / f'gpu_{index}.lock').open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        # Recheck after acquiring our coordination lock.
        fresh = dict(free_gpus()).get(index, 0)
        if fresh < minimum_mib:
            handle.close()
            continue
        return index, handle, fresh
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--status', type=Path, required=True)
    parser.add_argument('--min-free-gib', type=float, default=40)
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.min_free_gib <= 0 or args.interval <= 0:
        parser.error('An evaluator command and positive memory/interval are required')
    host = socket.gethostname()
    root = args.state_root.resolve() / host
    root.mkdir(parents=True, exist_ok=True)
    args.status.parent.mkdir(parents=True, exist_ok=True)

    def record(**values):
        data = {'host':host, 'launcher_pid':os.getpid(), 'updated_at':time.time(), **values}
        temporary = args.status.with_name(args.status.name + f'.tmp.{os.getpid()}')
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(args.status)

    while True:
        selected = claim_gpu(root, int(args.min_free_gib * 1024))
        if selected:
            break
        record(phase='waiting_for_free_gpu', required_free_gib=args.min_free_gib)
        time.sleep(args.interval)
    index, handle, available = selected
    env = os.environ.copy()
    env['CUDA_VISIBLE_DEVICES'] = str(index)
    try:
        process = subprocess.Popen(command, env=env)
        record(phase='running', gpu=index, free_mib_at_launch=available, evaluator_pid=process.pid)
        code = process.wait()
        record(phase='finished' if code == 0 else 'failed', gpu=index, returncode=code)
        return code
    finally:
        handle.close()


if __name__ == '__main__':
    raise SystemExit(main())
