import errno
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pixelworld.runner import _stop_snapshot_watcher


def test_quota_error_still_reaps_observer_before_propagating(monkeypatch, tmp_path):
    events = []

    def denied(_path, *args, **kwargs):
        raise OSError(errno.EDQUOT, 'Disk quota exceeded')

    class Observer:
        def poll(self):
            return None

        def terminate(self):
            events.append('terminate')

        def wait(self, timeout=None):
            events.append('wait')
            return -15

    monkeypatch.setattr(Path, 'touch', denied)
    with pytest.raises(OSError) as error:
        _stop_snapshot_watcher(Observer(), tmp_path / 'stop')
    assert error.value.errno == errno.EDQUOT
    assert events == ['terminate', 'wait']


def test_unresponsive_observer_is_reaped_without_a_model_process(tmp_path):
    events = []

    class Observer:
        attempts = 0

        def terminate(self):
            events.append('terminate')

        def kill(self):
            events.append('kill')

        def wait(self, timeout=None):
            self.attempts += 1
            if self.attempts < 3:
                raise subprocess.TimeoutExpired('snapshot observer', timeout)
            events.append('reaped')

    _stop_snapshot_watcher(Observer(), tmp_path / 'stop')
    assert events == ['terminate', 'kill', 'reaped']


def test_observer_exits_when_its_owner_is_gone_without_a_stop_marker(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    trace = tmp_path / 'trace'
    stop = tmp_path / 'stop'
    process = subprocess.Popen([
        sys.executable, '-m', 'pixelworld.snapshot', '--workspace', str(workspace),
        '--trace', str(trace), '--stop-file', str(stop),
        '--owner-pid', str(os.getpid() + 1000000),
    ])
    try:
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert not stop.exists()
    assert json.loads((trace / 'watcher_complete.json').read_text())['rounds'] == 0
    assert not list(trace.glob('round_*'))
