import errno
import json
import sys

import pytest

from tools import manage_matrix, watch_failed_retries


def test_manager_preserves_prior_state_on_quota_failure(tmp_path, monkeypatch):
    path = tmp_path/'manager.json'
    path.write_text(json.dumps({'phase': 'running', 'supervisor_pid': 7}))
    original = path.read_bytes()
    real_write = type(path).write_text

    def quota(target, *args, **kwargs):
        if target.suffix == '.tmp':
            raise OSError(errno.EDQUOT, 'quota')
        return real_write(target, *args, **kwargs)

    monkeypatch.setattr(type(path), 'write_text', quota)
    assert manage_matrix.write(path, {'phase': 'complete'}) is False
    assert path.read_bytes() == original


@pytest.mark.parametrize('code', [errno.EDQUOT, errno.ENOSPC, errno.EROFS])
def test_watched_retry_controller_survives_storage_failure(monkeypatch, code):
    calls = []
    waits = []

    def iteration():
        calls.append(True)
        if len(calls) == 1:
            raise OSError(code, 'storage temporarily unavailable')
        return 'restored'

    monkeypatch.setattr(watch_failed_retries, 'run', iteration)
    monkeypatch.setattr(sys, 'argv', ['retry', '--watch', '--interval', '2'])
    monkeypatch.setattr(watch_failed_retries.time, 'sleep', waits.append)
    assert watch_failed_retries.main() == 'restored'
    assert len(calls) == 2 and waits == [2]


def test_one_shot_retry_reports_storage_failure_to_caller(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['retry'])
    monkeypatch.setattr(watch_failed_retries, 'run', lambda: (_ for _ in ()).throw(OSError(errno.EDQUOT, 'quota')))
    with pytest.raises(OSError):
        watch_failed_retries.main()
