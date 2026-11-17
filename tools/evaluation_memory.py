"""Read container memory limits without substituting host physical memory."""
from pathlib import Path


def container_memory(root=Path('/sys/fs/cgroup')):
    def number(path):
        try:
            value=path.read_text().strip()
            return int(value) if value!='max' else None
        except (OSError,ValueError):return None
    limit=number(root/'memory.max');used=number(root/'memory.current');kind='v2'
    if limit is None:
        limit=number(root/'memory/memory.limit_in_bytes')
        used=number(root/'memory/memory.usage_in_bytes');kind='v1'
    if limit is not None and limit>=2**60:limit=None
    return {'source':kind if limit is not None else 'not_exposed',
            'limit_bytes':limit,'used_bytes':used,
            'available_bytes':max(0,limit-used) if limit is not None and used is not None else None}


def check_pool_memory(workers,frames,root=Path('/sys/fs/cgroup')):
    info=container_memory(root)
    # The official evaluator documents about9GB/process. Native120-frame
    # comparisons reserve12GiB each;20-frame development comparisons2GiB.
    estimated=workers*2*1024**3*max(1,frames/20)+1024**3
    info['required_bytes_estimate']=int(estimated)
    if info['available_bytes'] is not None and info['available_bytes']<estimated:
        raise MemoryError(f"Evaluator waiting for container memory: available={info['available_bytes']}, estimated_required={int(estimated)}, workers={workers}, frames={frames}")
    return info
