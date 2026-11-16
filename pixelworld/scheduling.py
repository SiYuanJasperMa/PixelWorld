"""Optional shared launch limit for concurrent benchmark supervisors."""
from contextlib import contextmanager
import fcntl
import json
import shlex
import subprocess
from pathlib import Path


def cgroup_memory() -> tuple[int, int] | None:
    """Return non-reclaimable working-set usage and hard limit for cgroup v1/v2."""
    candidates = [
        (Path('/sys/fs/cgroup/memory/memory.usage_in_bytes'),
         Path('/sys/fs/cgroup/memory/memory.limit_in_bytes'),Path('/sys/fs/cgroup/memory/memory.stat')),
        (Path('/sys/fs/cgroup/memory.current'),Path('/sys/fs/cgroup/memory.max'),Path('/sys/fs/cgroup/memory.stat')),
    ]
    for usage_path,limit_path,stat_path in candidates:
        try:
            usage=int(usage_path.read_text().strip());raw=limit_path.read_text().strip()
            if raw == 'max':continue
            limit=int(raw)
            stats={row.split()[0]:int(row.split()[1]) for row in stat_path.read_text().splitlines()
                   if len(row.split())==2}
            # Linux can reclaim inactive file cache under pressure. Kubernetes
            # uses the same working-set definition rather than treating cached
            # PNG/NPZ reads as committed process memory.
            usage=max(0,usage-stats.get('total_inactive_file',stats.get('inactive_file',0)))
            # cgroup v1 reports a very large sentinel when no limit is active.
            if 0 <= usage < limit < 1 << 60:return usage,limit
        except (OSError,ValueError):
            continue
    return None


def memory_launch_allowed(settings: dict) -> bool:
    required=settings.get('min_cgroup_memory_headroom_bytes')
    if required is None:return True
    if not isinstance(required,int) or isinstance(required,bool) or required < 0:
        raise ValueError('min_cgroup_memory_headroom_bytes must be a non-negative integer')
    value=cgroup_memory()
    return value is None or value[1]-value[0] >= required


@contextmanager
def launch_guard(config):
    root = config.runs_root.parent.parent
    settings = root / "scheduling_limits.json"
    if not settings.is_file():
        yield None, {}, {}
        return
    limit = json.loads(settings.read_text())["max_per_model"]
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("Shared model concurrency must be a positive integer")
    with settings.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counts = {}
        config_counts = {}
        for line in subprocess.check_output(["ps", "-eo", "args="], text=True).splitlines():
            try:
                tokens = shlex.split(line)
                if not any(tokens[i:i+3] == ["-m", "pixelworld.cli", "run-case"]
                           for i in range(len(tokens)-2)):
                    continue
                from pathlib import Path
                source = Path(tokens[tokens.index("--config") + 1]).resolve()
                if not source.is_relative_to(root.resolve()):
                    continue
                model = tokens[tokens.index("--model") + 1]
                counts[model] = counts.get(model, 0) + 1
                config_counts[str(source)] = config_counts.get(str(source), 0) + 1
            except (ValueError, IndexError):
                continue
        yield limit, counts, config_counts
