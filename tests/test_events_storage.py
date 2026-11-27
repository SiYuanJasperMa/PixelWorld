import errno
import io
import json
from pathlib import Path

import pytest

from pixelworld import events


@pytest.mark.parametrize('failure', ['raw', 'annotated', 'fsync'])
def test_partial_event_storage_failure_preserves_both_streams_once(tmp_path, monkeypatch, failure):
    raw = tmp_path/'events.raw.jsonl'
    annotated = tmp_path/'events.jsonl'
    prefix = b'{"type":"thread.started"}\n'
    raw.write_bytes(prefix)
    annotated.write_bytes(prefix)
    line = b'{"type":"turn.completed","usage":{"output_tokens":9}}\n'
    original_open = Path.open
    original_sync = events.os.fsync
    failed = [False]
    waits = []
    timestamps = []

    class FaultyFile:
        def __init__(self, target):
            self.target = target

        def __getattr__(self, name):
            return getattr(self.target, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.target.close()

        def write(self, data):
            if not failed[0]:
                failed[0] = True
                self.target.write(data[:7])
                raise OSError(errno.EDQUOT, 'Quota exceeded after partial write')
            return self.target.write(data)

    def opened(path, *args, **kwargs):
        target = original_open(path, *args, **kwargs)
        if kwargs.get('buffering') == 0 and path == {'raw': raw, 'annotated': annotated}.get(failure):
            return FaultyFile(target)
        return target

    def synced(fd):
        if failure == 'fsync' and not failed[0]:
            failed[0] = True
            raise OSError(errno.EIO, 'Temporary writeback failure')
        return original_sync(fd)

    def timestamp():
        timestamps.append(True)
        return 12345

    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(events.os, 'fsync', synced)
    monkeypatch.setattr(events.time, 'sleep', waits.append)
    monkeypatch.setattr(events.time, 'monotonic_ns', timestamp)
    state = {}
    events.capture_stream(io.BytesIO(line), raw, annotated, state)
    assert raw.read_bytes() == prefix + line
    assert annotated.read_bytes().startswith(prefix)
    records = annotated.read_bytes()[len(prefix):].splitlines()
    assert len(records) == 1
    assert json.loads(records[0])['_harness_received']['monotonic_ns'] == 12345
    assert len(timestamps) == 1
    assert waits == [1]
    assert state['status'] == 'complete' and state['storage_waits'] == 1


def test_capture_recovers_output_open_failure_and_keeps_malformed_bytes(tmp_path, monkeypatch):
    raw = tmp_path/'events.raw.jsonl'
    annotated = tmp_path/'events.jsonl'
    original_open = Path.open
    failed = [False]

    def opened(path, *args, **kwargs):
        if path == annotated and not failed[0]:
            failed[0] = True
            raise OSError(errno.ENOSPC, 'Temporary full disk')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(events.time, 'sleep', lambda _: None)
    data = b'not json\xff\n[1,2]\n'
    state = {}
    events.capture_stream(io.BytesIO(data), raw, annotated, state)
    assert raw.read_bytes() == annotated.read_bytes() == data
    assert state['status'] == 'complete' and state['storage_waits'] == 1


def test_unrecoverable_capture_error_is_exposed_to_the_supervisor(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, 'Output access denied')

    monkeypatch.setattr(Path, 'open', denied)
    state = {}
    with pytest.raises(PermissionError):
        events.capture_stream(io.BytesIO(b'{}\n'), tmp_path/'raw', tmp_path/'events', state)
    assert state['status'] == 'failed' and state['last_errno'] == errno.EACCES


def test_supervisor_stops_only_its_agent_after_a_proven_capture_failure(tmp_path, monkeypatch):
    from pixelworld.runner import _wait
    from pixelworld import token_budget
    from pixelworld.retries import infrastructure_failure

    class Agent:
        returncode = None

        def poll(self):
            return self.returncode

    agent = Agent()
    stopped = []

    def stop(target):
        stopped.append(target)
        target.returncode = -15

    monkeypatch.setattr(token_budget, 'stop_agent', stop)
    code, watchdog = _wait(agent, tmp_path/'events.jsonl', tmp_path,
                           capture_state={'status': 'failed', 'last_errno': errno.EACCES})
    assert stopped == [agent] and code == -15
    assert watchdog['kind'] == 'stream_capture'
    assert infrastructure_failure({'validation': {'infra_error': watchdog['reason']}})
    assert (tmp_path/'stream_capture_failure.json').is_file()


def test_output_budget_still_takes_precedence_over_capture_failure(tmp_path, monkeypatch):
    from pixelworld.runner import _wait
    from pixelworld import token_budget

    class Agent:
        returncode = None

        def poll(self):
            return self.returncode

    class Monitor:
        record_count = 1
        unavailable = False
        crossed_utc = '2026-11-27T00:00:00+00:00'

        def poll(self):
            return True

        def report(self):
            return {'exhausted': True}

    def stop(agent):
        agent.returncode = -15

    monkeypatch.setattr(token_budget, 'stop_agent', stop)
    code, watchdog = _wait(Agent(), tmp_path/'events.jsonl', tmp_path,
                           token_monitor=Monitor(), capture_state={'status': 'failed'})
    assert code == -15
    assert watchdog['kind'] == 'token_budget'
    assert watchdog['reason'] == 'output_token_budget_exhausted'
