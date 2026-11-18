import json
import threading

import pytest

from pixelworld.cloud_log_writer import CloudLogWriter


def test_submit_does_not_wait_for_cloud_write(tmp_path):
    writer = CloudLogWriter(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = writer._write
    def slow(name, data, append):
        entered.set(); release.wait(3); original(name, data, append)
    writer._write = slow
    writer.submit('response.json', {'message': '像素✓'})
    assert entered.wait(1)
    assert not (tmp_path / 'response.json').exists()
    assert writer.status()['pending_records'] == 1
    release.set(); writer.flush()
    assert json.loads((tmp_path / 'response.json').read_text()) == {'message': '像素✓'}
    assert writer.status()['queued_bytes'] == 0
    writer.close()


def test_queue_limit_and_safe_names(tmp_path):
    writer = CloudLogWriter(tmp_path, max_queue_bytes=64)
    with pytest.raises(RuntimeError, match='exceeds'):
        writer.submit('big.json', {'value': 'x' * 100})
    with pytest.raises(ValueError):
        writer.submit('../outside.json', {})
    writer.close()


def test_cloud_write_error_is_visible_to_next_request(tmp_path):
    writer = CloudLogWriter(tmp_path)
    def fail(*args):raise OSError('cloud unavailable')
    writer._write = fail
    writer.submit('record.json', {})
    with pytest.raises(RuntimeError, match='cloud unavailable'):
        writer.flush()
    with pytest.raises(RuntimeError, match='cloud log writer failed'):
        writer.submit('next.json', {})
