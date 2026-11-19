import os

import pytest

from tools.manage_matrix import dispatcher_disk_stall
from tools import manage_matrix as manager


def fake_dispatcher(tmp_path, state='D', children=(), config='config.json'):
    status = tmp_path / 'status.json'
    status.write_text('{}')
    os.utime(status, (100, 100))
    proc = tmp_path / 'proc' / '123'
    (proc / 'task/123').mkdir(parents=True)
    (proc / 'stat').write_text(f'123 (python) {state} 0')
    args = ['python', '-m', 'pixelworld.cli', 'matrix', 'run', '--config', config]
    (proc / 'cmdline').write_bytes(b'\0'.join(x.encode() for x in args) + b'\0')
    (proc / 'task/123/children').write_text(' '.join(str(pid) for pid, _ in children))
    for pid, child_state in children:
        child = tmp_path / 'proc' / str(pid)
        child.mkdir()
        (child / 'stat').write_text(f'{pid} (python) {child_state} 0')
    return status, tmp_path / 'proc'


def check(status, proc, now=2000, stale_seconds=900):
    return dispatcher_disk_stall(123, status, ('pixelworld.cli', 'matrix', 'run', 'config.json'),
                                 stale_seconds, proc_root=proc, now=now)


def test_stale_disk_blocked_dispatcher_with_only_ended_children_is_recoverable(tmp_path):
    status, proc = fake_dispatcher(tmp_path, children=[(456, 'Z')])
    assert check(status, proc)


@pytest.mark.parametrize('state', ['S', 'R', 'T', 'Z'])
def test_long_wait_without_disk_block_never_requests_recovery(tmp_path, state):
    status, proc = fake_dispatcher(tmp_path, state=state)
    assert not check(status, proc)


def test_active_model_child_prevents_dispatcher_recovery(tmp_path):
    status, proc = fake_dispatcher(tmp_path, children=[(456, 'S')])
    assert not check(status, proc)


def test_fresh_status_disabled_recovery_and_other_job_are_preserved(tmp_path):
    status, proc = fake_dispatcher(tmp_path, config='other.json')
    assert not check(status, proc)
    (proc / '123/cmdline').write_bytes(b'python\0pixelworld.cli\0matrix\0run\0config.json\0')
    assert not check(status, proc, now=200)
    assert not check(status, proc, stale_seconds=0)


def test_unknown_process_observation_is_not_a_recovery_signal(tmp_path):
    status, proc = fake_dispatcher(tmp_path)
    (proc / '123/stat').unlink()
    assert not check(status, proc)


def test_recovery_signals_pinned_dispatcher_and_closes_handle(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(manager.os, 'pidfd_open', lambda pid: calls.append(('open', pid)) or 10)
    monkeypatch.setattr(manager.os, 'close', lambda fd: calls.append(('close', fd)))
    monkeypatch.setattr(manager, 'dispatcher_disk_stall', lambda *args: True)
    monkeypatch.setattr(manager.signal, 'pidfd_send_signal', lambda fd, sig: calls.append(('signal', fd, sig)))
    assert manager.recover_disk_dispatcher(123, tmp_path / 'status.json', ('matrix',), 900)
    assert calls == [('open', 123), ('signal', 10, manager.signal.SIGTERM), ('close', 10)]
