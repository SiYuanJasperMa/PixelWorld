import errno
import json
from pathlib import Path

import pytest

from pixelworld.util import atomic_bytes, atomic_json, write_stream_bytes


def test_storage_wait_preserves_old_record_and_retries_exact_new_bytes(tmp_path, monkeypatch):
    target = tmp_path / 'run_manifest.json'
    target.write_text('{"status":"running"}')
    real_open = Path.open
    writes = []
    waits = []

    def blocked_once(path, *args, **kwargs):
        if '.tmp.' in path.name and args and args[0] == 'wb':
            writes.append(path)
            if len(writes) == 1:
                assert json.loads(target.read_text())['status'] == 'running'
                raise OSError(errno.EDQUOT, 'Disk quota exceeded')
        return real_open(path, *args, **kwargs)

    monkeypatch.setenv('PIXELWORLD_PERSIST_STORAGE_WAIT', '1')
    monkeypatch.setattr(Path, 'open', blocked_once)
    monkeypatch.setattr('pixelworld.util.time.sleep', waits.append)
    record = {'status': 'complete', 'output_tokens': 123, 'unreported': None}
    atomic_json(target, record)
    assert json.loads(target.read_text()) == record
    assert len(writes) == 2 and waits == [2]
    assert not list(tmp_path.glob('*.tmp.*'))


def test_unconfigured_or_nonstorage_failure_is_not_hidden(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise OSError(errno.EACCES, 'Permission denied')

    monkeypatch.setenv('PIXELWORLD_PERSIST_STORAGE_WAIT', '1')
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(OSError) as error:
        atomic_json(tmp_path / 'record.json', {})
    assert error.value.errno == errno.EACCES


def test_stream_storage_failure_retries_only_unwritten_bytes(monkeypatch):
    import io
    class InterruptedLog(io.BytesIO):
        calls = 0
        def write(self, data):
            self.calls += 1
            if self.calls == 1:
                return super().write(data[:3])
            if self.calls == 2:
                raise OSError(errno.EDQUOT, 'Disk quota exceeded')
            return super().write(data)
    log = InterruptedLog(b'prior:')
    log.seek(0, 2)
    waits = []
    monkeypatch.setenv('PIXELWORLD_PERSIST_STORAGE_WAIT', '1')
    monkeypatch.setattr('pixelworld.util.time.sleep', waits.append)
    write_stream_bytes(log, b'original API response')
    assert log.getvalue() == b'prior:original API response'
    assert waits == [2]


def test_atomic_bytes_preserves_verbatim_body(tmp_path):
    body = b'\x00\xff{"model":"test"}\r\n'
    target = tmp_path/'input.body'
    atomic_bytes(target, body)
    assert target.read_bytes() == body
