"""Read worker-owned progress without treating a remote PID as a local one."""
import time
from pathlib import Path
import json


def remote_case_status(config, model, track, case, inspected, now=None, max_age=120):
    if inspected['state'] == 'complete':
        return inspected
    path = config.runs_root / 'status.json'
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
        status = json.loads(path.read_text())
        group = status['groups'][model.key + '/' + track]
        if age < max_age:
            if case in group.get('running_cases', []):
                return {**inspected, 'state': 'running', 'state_source': 'fresh worker status'}
            if case in group.get('exhausted_cases', []):
                return {**inspected, 'state': 'exhausted', 'state_source': 'fresh worker status'}
        # A running manifest and an unverified foreign PID are infrastructure
        # uncertainty, not evidence that the model ended without delivering.
        for manifest in (config.runs_root / model.key / track).glob(f'case_{case}_attempt_*/run_manifest.json'):
            if json.loads(manifest.read_text()).get('status') == 'running':
                return {**inspected, 'state': 'infra_stale', 'state_source': 'remote worker status missing or stale'}
    except (OSError, ValueError, KeyError):
        return {**inspected, 'state': 'infra_stale', 'state_source': 'remote worker status unavailable'}
    return inspected
