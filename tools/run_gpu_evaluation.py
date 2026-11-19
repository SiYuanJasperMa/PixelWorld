"""Run an evaluator on a GPU with enough free memory, holding a shared claim.

The claim coordinates PixelWorld jobs only. Other workloads are never stopped.
All state and locks are written under the caller's evaluation-side state root.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time


# Verified versions yield their small CUDA worker when another compute app
# arrives. A process name alone is insufficient evidence for this exception.
YIELDING_KEEPALIVE_HASHES = frozenset({
    'ce37530f18b3592b0080a5b697b0ad0191633f28255865797bc5345772507a32',
    '477b37963885f79964057036c5d04bdddb97dd3c82735c160244c6daba990446',
})


def known_keepalive_devices(proc_root=Path('/proc'), allowed_hashes=YIELDING_KEEPALIVE_HASHES):
    devices=set()
    for proc in proc_root.glob('[0-9]*'):
        try:
            if (proc/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':continue
            argv=[a.decode()for a in(proc/'cmdline').read_bytes().split(b'\0')if a]
            if '--worker' not in argv or '--gpu' not in argv:continue
            script=next((a for a in argv if a.endswith('spring_keepalive.py')),None)
            if not script:continue
            path=Path(script)
            if not path.is_absolute():path=(proc/'cwd').resolve()/path
            if hashlib.sha256(path.read_bytes()).hexdigest() not in allowed_hashes:continue
            devices.add(int(argv[argv.index('--gpu')+1]))
        except (OSError,ValueError,UnicodeError,IndexError):continue
    return devices


def gpu_app_snapshot():
    """Map NVML host PIDs by GPU UUID; never compare them to container PIDs."""
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,memory.used',
            '--format=csv,noheader,nounits'],check=True,capture_output=True,text=True,timeout=30)
        rows={};mapping={}
        for line in result.stdout.splitlines():
            index,uuid,used=(s.strip()for s in line.split(','))
            if not uuid.startswith('GPU-'):return None
            mapping[uuid]=int(index);rows[int(index)]={'apps':0,'used_mib':int(used)}
        result=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid',
            '--format=csv,noheader,nounits'],check=True,capture_output=True,text=True,timeout=30)
        for line in result.stdout.splitlines():
            if not line.strip() or 'No running' in line:continue
            uuid,pid=(s.strip()for s in line.split(','))
            int(pid)
            if uuid in mapping:rows[mapping[uuid]]['apps']+=1
        return rows
    except (subprocess.SubprocessError,ValueError):return None


def free_gpus() -> list[tuple[int, int]]:
    result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.free,utilization.gpu',
                             '--format=csv,noheader,nounits'], check=True,
                            capture_output=True, text=True, timeout=30)
    rows = [];apps=gpu_app_snapshot();keepers=known_keepalive_devices() if apps is not None else set()
    for line in result.stdout.splitlines():
        index, memory, utilization = line.split(',')
        index=int(index.strip())
        low_util=utilization.strip().isdigit() and int(utilization.strip()) <= 20
        if apps is not None:
            info=apps.get(index)
            if info is None:continue
            keeper_only=(index in keepers and info['apps']==1 and info['used_mib']<=1024)
            available=(info['apps']==0 and low_util) or keeper_only
        else:
            # Preserve the original low-utilization policy when NVML process
            # metadata is unavailable; never exempt an unknown busy device.
            available=low_util
        if available:rows.append((index,int(memory.strip())))
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
