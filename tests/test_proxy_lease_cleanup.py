import errno
from types import SimpleNamespace

import pytest

from pixelworld.traex_responses_proxy import release_log_marker


@pytest.mark.parametrize('error', [errno.EDQUOT, errno.EIO])
def test_failed_marker_cleanup_releases_lease_and_keeps_originals(tmp_path, error):
    original = tmp_path/'resp_one.input.json'
    original.write_bytes(b'complete original input')
    marker = tmp_path/'resp_one.active.json'
    marker.write_bytes(b'{}')
    released = []

    class Budget:
        def remove_record(self, name):
            raise OSError(error, 'Marker cleanup failed')

        def release(self, request):
            released.append(request)

    with pytest.raises(OSError):
        release_log_marker(SimpleNamespace(log_dir=tmp_path), 'resp_one', Budget(), True)
    assert released == ['resp_one']
    assert original.read_bytes() == b'complete original input'
    assert marker.read_bytes() == b'{}'


def test_unleased_request_never_releases_another_reservation(tmp_path):
    released = []

    class Budget:
        def remove_record(self, name):
            pass

        def release(self, request):
            released.append(request)

    release_log_marker(SimpleNamespace(log_dir=tmp_path), 'resp_unleased', Budget(), False)
    assert released == []


def test_successful_plain_marker_cleanup_does_not_remove_request_bytes(tmp_path):
    (tmp_path/'resp_one.active.json').write_bytes(b'{}')
    (tmp_path/'resp_one.response.json').write_bytes(b'complete original output')
    release_log_marker(SimpleNamespace(log_dir=tmp_path), 'resp_one', None, False)
    assert not (tmp_path/'resp_one.active.json').exists()
    assert (tmp_path/'resp_one.response.json').read_bytes() == b'complete original output'
