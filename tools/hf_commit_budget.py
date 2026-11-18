"""Coordinate dataset commits across publishers and respect Hub retry windows."""
import fcntl
import json
from pathlib import Path
import re
import time


DEFAULT_STATE = Path(__file__).resolve().parents[1] / 'runs/publication_state/hf_commit_budget.json'


def reserve(state, now, spacing=45):
    """Reserve a commit slot under a short cross-process file lock."""
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(state.read_text()) if state.exists() else {}
        slot = max(now, data.get('last_reserved_unix', 0) + spacing,
                   data.get('cooldown_until_unix', 0))
        data.update(last_reserved_unix=slot, spacing_seconds=spacing)
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.replace(state)
    return slot


def defer(state, until, reason):
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(state.read_text()) if state.exists() else {}
        data['cooldown_until_unix'] = max(until, data.get('cooldown_until_unix', 0))
        data['cooldown_reason'] = reason
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.replace(state)


def retry_delay(error):
    response = getattr(error, 'response', None)
    if response is None or response.status_code != 429:
        return None
    value = response.headers.get('Retry-After', '')
    if value.isdigit():
        return max(1, int(value))
    match = re.search(r'retry.*?about\s+(\d+)\s+(hour|minute|second)', str(error), re.I)
    if match:
        return int(match.group(1)) * {'hour': 3600, 'minute': 60, 'second': 1}[match.group(2).lower()]
    return 300


def commit(api, state=DEFAULT_STATE, operations_factory=None, **kwargs):
    """Keep existing atomic operations; only coordinate the submission schedule."""
    slot = reserve(state, time.time())
    while True:
        data = json.loads(Path(state).read_text())
        # Re-reserve when a new server cooldown invalidates an earlier slot;
        # waiting publishers must not all submit at the cooldown boundary.
        if data.get('cooldown_until_unix', 0) > slot:
            slot = reserve(state, time.time())
        remaining = slot - time.time()
        if remaining <= 0:
            break
        time.sleep(min(10, remaining))
    try:
        if operations_factory is not None:
            kwargs['operations'] = operations_factory()
        return api.create_commit(**kwargs)
    except Exception as error:
        delay = retry_delay(error)
        if delay is not None:
            defer(state, time.time() + delay, 'HF commit API returned 429; respect its retry window')
        raise
