import json
import time
from pathlib import Path

import pytest

from pixelworld.log_storage import SpoolBudget, StorageBackpressure
from pixelworld.runner import _provider_infra_failure


def test_native_4055_is_infrastructure_not_model_non_delivery(tmp_path):
    import json
    events=tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'turn.failed','error':{'message':'Upstream error: Code 4055: Too many requests were sent in a short period.'}})+'\n')
    assert _provider_infra_failure(events)=='provider_rate_limited'
    events.write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'I think Code 4055 happened'}})+'\n')
    assert _provider_infra_failure(events) is None
from pixelworld.retries import infrastructure_failure
from tools.spool_api_logs import copy_record, prune_record


def test_standard_capacity_is_shared_64gib_without_a_launcher_override():
    import argparse
    from pixelworld.log_storage import add_storage_arguments, resolve_storage_arguments
    parser=argparse.ArgumentParser()
    add_storage_arguments(parser)
    args=resolve_storage_arguments(parser.parse_args([]),parser)
    assert args.spool_max_bytes==64*1024**3
    assert args.min_free_bytes==20*1024**3


def test_explicit_legacy_async_does_not_silently_enable_both_storage_modes():
    import argparse
    from pixelworld.log_storage import add_storage_arguments, resolve_storage_arguments
    parser=argparse.ArgumentParser()
    add_storage_arguments(parser)
    args=resolve_storage_arguments(parser.parse_args(['--async-cloud-logs']),parser)
    assert args.spool_max_bytes==0
    with pytest.raises(SystemExit):
        resolve_storage_arguments(parser.parse_args([
            '--async-cloud-logs','--spool-max-bytes',str(64*1024**3)]),parser)
    with pytest.raises(SystemExit):
        resolve_storage_arguments(parser.parse_args(['--spool-max-bytes','-1']),parser)


def test_admission_reserves_capacity_across_concurrent_requests(tmp_path):
    budget=SpoolBudget(tmp_path,150*1024**2)
    budget.acquire('one',100,timeout=0)
    budget.acquire('two',100,timeout=0)
    with pytest.raises(StorageBackpressure):budget.acquire('three',100,timeout=0)
    budget.release('one')
    budget.acquire('three',100,timeout=0)


def test_draining_segment_keeps_inflight_bytes_and_rejects_new_sampling(tmp_path):
    from pixelworld.log_storage import DRAIN_MARKER
    budget = SpoolBudget(tmp_path, 1024**3)
    budget.acquire('active', 100, timeout=0)
    budget.write('active', 'active.input.json', b'complete original input')
    (tmp_path/DRAIN_MARKER).write_text('operator requested safe rotation')
    status = budget.capacity_status(100, refresh=False)
    assert status['draining'] and not status['ready']
    assert status['active_reservations'] == 1
    with pytest.raises(StorageBackpressure, match='segment draining'):
        budget.acquire('new', 100, timeout=0)
    assert set(budget.reservations) == {'active'}
    budget.write('active', 'active.response.json', b'complete original response')
    budget.append_summary('active', {'id': 'active', 'status': 'complete'})
    budget.release('active')
    assert budget.capacity_status(100)['active_reservations'] == 0
    assert not budget.capacity_status(100)['ready']
    assert (tmp_path/'active.input.json').read_bytes() == b'complete original input'
    assert (tmp_path/'active.response.json').read_bytes() == b'complete original response'


def test_waiting_admission_observes_drain_without_taking_a_lease(tmp_path, monkeypatch):
    from pixelworld.log_storage import DRAIN_MARKER
    import pixelworld.log_storage as storage
    budget = SpoolBudget(tmp_path, 100*1024**2)
    budget.acquire('active', 100, timeout=0)
    sleeps = []

    def request_drain(delay):
        sleeps.append(delay)
        (tmp_path/DRAIN_MARKER).write_text('drain')

    monkeypatch.setattr(storage.time, 'sleep', request_drain)
    with pytest.raises(StorageBackpressure, match='segment draining'):
        budget.acquire('waiting', 100, timeout=60)
    assert len(sleeps) == 1
    assert set(budget.reservations) == {'active'}


def test_drain_does_not_transfer_to_a_new_segment(tmp_path):
    from pixelworld.log_storage import DRAIN_MARKER
    old = tmp_path/'old'; new = tmp_path/'new'
    old.mkdir(); (old/DRAIN_MARKER).write_text('old segment retained')
    closed = SpoolBudget(old, 1024**3)
    fresh = SpoolBudget(new, 1024**3)
    assert closed.capacity_status()['draining']
    assert fresh.capacity_status()['ready']
    fresh.acquire('new', 100, timeout=0)
    assert (old/DRAIN_MARKER).is_file()


def test_waiting_admissions_share_capacity_reconciliation(tmp_path, monkeypatch):
    import pixelworld.log_storage as storage
    clock = [100.0]
    monkeypatch.setattr(storage.time, 'monotonic', lambda: clock[0])
    budget = SpoolBudget(tmp_path, 150 * 1024**2)
    original = budget.used_bytes
    scans = []

    def counted():
        scans.append(clock[0])
        return original()

    monkeypatch.setattr(budget, 'used_bytes', counted)
    budget.acquire('one', 100, timeout=0)
    budget.acquire('two', 100, timeout=0)
    for i in range(40):
        with pytest.raises(StorageBackpressure):
            budget.acquire('waiting_'+str(i), 100, timeout=0)
    assert len(scans) == 1
    clock[0] += storage.CAPACITY_RECONCILE_INTERVAL_SECONDS
    with pytest.raises(StorageBackpressure):
        budget.acquire('waiting_next', 100, timeout=0)
    assert len(scans) == 2
    budget.write('one', 'resp_one.input.json', b'exact original input')
    budget.release('one')
    budget.acquire('waiting_next', 100, timeout=0)
    assert len(scans) == 2
    assert (tmp_path/'resp_one.input.json').read_bytes() == b'exact original input'


def test_external_removal_reclaims_capacity_without_waiting_for_scan_interval(tmp_path, monkeypatch):
    import os
    import pixelworld.log_storage as storage
    monkeypatch.setattr(storage.time, 'monotonic', lambda: 100.0)
    retained = tmp_path/'retained.input.json'
    with retained.open('wb') as stream:
        stream.truncate(40 * 1024**2)
    budget = SpoolBudget(tmp_path, 100 * 1024**2)
    with pytest.raises(StorageBackpressure):
        budget.acquire('one', 100, timeout=0)
    retained.unlink()
    # Some filesystems share one timestamp across operations in the same tick.
    # Model an observed directory update without relying on a wall-clock sleep.
    os.utime(tmp_path, ns=(tmp_path.stat().st_atime_ns, budget._directory_stamp + 1))
    budget.acquire('one', 100, timeout=0)
    assert budget.capacity_status(0, refresh=False)['used_bytes'] == 0


def test_capacity_reconciliation_recovers_removal_with_unchanged_directory_timestamp(tmp_path, monkeypatch):
    import os
    import pixelworld.log_storage as storage
    clock = [100.0]
    monkeypatch.setattr(storage.time, 'monotonic', lambda: clock[0])
    retained = tmp_path/'retained.input.json'
    with retained.open('wb') as stream:
        stream.truncate(40 * 1024**2)
    budget = SpoolBudget(tmp_path, 100 * 1024**2)
    with pytest.raises(StorageBackpressure):
        budget.acquire('one', 100, timeout=0)
    stamp = budget._directory_stamp
    retained.unlink()
    os.utime(tmp_path, ns=(tmp_path.stat().st_atime_ns, stamp))
    with pytest.raises(StorageBackpressure):
        budget.acquire('one', 100, timeout=0)
    clock[0] += storage.CAPACITY_RECONCILE_INTERVAL_SECONDS
    budget.acquire('one', 100, timeout=0)
    assert budget.capacity_status(0, refresh=False)['used_bytes'] == 0


def test_readiness_includes_retained_logs_and_active_reservations(tmp_path):
    budget=SpoolBudget(tmp_path,100*1024**2)
    assert budget.capacity_status(100)['ready']
    budget.acquire('one',100,timeout=0)
    status=budget.capacity_status(100)
    assert not status['ready'] and status['active_reservations']==1
    budget.release('one')
    assert budget.capacity_status(100)['ready']
    with (tmp_path/'retained.input.json').open('wb') as stream:
        stream.truncate(40*1024**2)
    status=budget.capacity_status(100)
    assert not status['ready'] and status['used_bytes']==40*1024**2
    with pytest.raises(StorageBackpressure):budget.acquire('two',100,timeout=0)
    assert not budget.reservations


def test_readiness_checks_free_disk_space_without_exposing_path(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import pixelworld.log_storage as storage
    budget=SpoolBudget(tmp_path,1024**3,min_free_bytes=100)
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda root:SimpleNamespace(free=64*1024**2))
    status=budget.capacity_status()
    assert not status['ready'] and status['snapshot_only']
    assert str(tmp_path) not in json.dumps(status)
    with pytest.raises(ValueError):budget.capacity_status(-1)


def test_exact_logged_bytes_and_oversized_record_is_rejected(tmp_path):
    budget=SpoolBudget(tmp_path,100*1024**2)
    budget.acquire('one',100,timeout=0)
    value=b'{"image":"test","text":"exact"}'
    budget.write('one','resp_one.input.json',value)
    assert (tmp_path/'resp_one.input.json').read_bytes()==value
    budget.reservations['one']=3
    with pytest.raises(StorageBackpressure):budget.write('one','too_big.json',b'1234')
    assert not (tmp_path/'too_big.json').exists()


def test_managed_logging_does_not_rescan_the_growing_directory(tmp_path, monkeypatch):
    budget = SpoolBudget(tmp_path, 1024**3)
    original = budget.used_bytes
    scans = []
    def scan():
        scans.append(True)
        return original()
    monkeypatch.setattr(budget, 'used_bytes', scan)
    for i in range(8):
        request = str(i)
        budget.acquire(request, 100, timeout=0)
        budget.write(request, request + '.active.json', b'{}')
        budget.write(request, request + '.input.json', b'complete original input')
        budget.append_summary(request, {'id': request})
        budget.remove_record(request + '.active.json')
        budget.release(request)
    status = budget.capacity_status(refresh=False)
    assert len(scans) == 1
    assert status['used_bytes'] == original()
    assert all((tmp_path / (str(i) + '.input.json')).read_bytes() == b'complete original input' for i in range(8))


def test_managed_requests_do_not_enumerate_the_namespace_each_time(tmp_path, monkeypatch):
    import pixelworld.log_storage as storage
    scans = []
    original = storage.os.scandir

    def counted(path):
        scans.append(path)
        return original(path)

    monkeypatch.setattr(storage.os, 'scandir', counted)
    budget = SpoolBudget(tmp_path, 1024**3)
    for i in range(30):
        request = str(i)
        budget.acquire(request, 10, timeout=0)
        budget.write(request, request+'.input.json', b'original request')
        budget.append_summary(request, {'id': request})
        budget.release(request)
        assert budget.capacity_status(refresh=False)['ready']
    assert len(scans) == 1
    assert (tmp_path/'29.input.json').read_bytes() == b'original request'


def test_new_external_closed_files_trigger_capacity_reconciliation(tmp_path):
    budget = SpoolBudget(tmp_path, 100 * 1024**2)
    assert budget.capacity_status()['ready']
    with (tmp_path / 'restored.json').open('wb') as stream:
        stream.truncate(40 * 1024**2)
    assert not budget.capacity_status()['ready']
    assert budget.capacity_status()['used_bytes'] == 40 * 1024**2


def test_mutable_spool_directory_has_one_writer(tmp_path):
    owner = SpoolBudget(tmp_path, 100 * 1024**2)
    with pytest.raises(RuntimeError, match='already owns'):
        SpoolBudget(tmp_path, 100 * 1024**2)
    owner._owner.close()
    replacement = SpoolBudget(tmp_path, 100 * 1024**2)
    assert replacement.capacity_status()['ready']


def test_verified_compressed_archive_is_readable_and_reclaimed(tmp_path):
    import gzip
    spool=tmp_path/'spool';spool.mkdir()
    source=spool/'resp_abcd.input.json';source.write_bytes(b'{"all":"original bytes"}')
    archive=tmp_path/'archive';receipt=copy_record(source,archive,compress=True)
    assert gzip.open(archive/receipt['archive'],'rb').read()==source.read_bytes()
    assert receipt['readback_verified']
    assert prune_record(source,receipt,min_age=0)
    assert not source.exists()
    assert (archive/receipt['archive']).is_file()


def test_active_or_changed_records_are_never_pruned(tmp_path):
    source=tmp_path/'resp_abcd.input.json';source.write_bytes(b'original')
    receipt=copy_record(source,tmp_path/'archive',compress=True)
    marker=tmp_path/'resp_abcd.active.json';marker.write_text('{}')
    assert not prune_record(source,receipt,min_age=0)
    marker.unlink();source.write_bytes(b'new version')
    assert not prune_record(source,receipt,min_age=0)


def test_disk_exhaustion_and_backpressure_do_not_consume_model_attempts(tmp_path):
    events=tmp_path/'events.jsonl'
    for message in ['502 Bad Gateway: [Errno 28] No space left on device',
                    '502 Bad Gateway: [Errno 122] Disk quota exceeded',
                    '503 storage_backpressure: durable log buffer full']:
        events.write_text(json.dumps({'type':'error','message':message}))
        reason=_provider_infra_failure(events)
        assert reason=='storage_infrastructure_failure'
        assert infrastructure_failure({'stop_reason':reason})
        assert not infrastructure_failure({'stop_reason':'output_token_budget_exhausted'})


def test_quota_error_requires_a_real_error_event(tmp_path):
    events=tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Disk quota exceeded'}}))
    assert _provider_infra_failure(events) is None


def test_large_local_proxy_request_is_infrastructure_not_model_failure(tmp_path):
    events=tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'error','message':
        'unexpected status 502 Bad Gateway: Request exceeds local size limit'}))
    reason=_provider_infra_failure(events)
    assert reason=='local_proxy_request_limit'
    assert infrastructure_failure({'stop_reason':reason})


def test_stream_send_disconnect_is_infrastructure(tmp_path):
    events=tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'error','message':
        'stream disconnected before completion: error sending request'}))
    assert _provider_infra_failure(events)=='provider_connection_failed'


def test_local_proxy_auth_mismatch_is_infrastructure(tmp_path):
    events=tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'error','message':
        'unexpected status 401 Unauthorized: Local proxy authentication required'}))
    reason=_provider_infra_failure(events)
    assert reason=='local_proxy_auth_failure'
    assert infrastructure_failure({'stop_reason':reason})


def test_first_token_stall_is_infrastructure_not_model_failure():
    from pixelworld.retries import infrastructure_failure
    assert infrastructure_failure({'stop_reason':'provider_first_token_stall'})
    assert not infrastructure_failure({'stop_reason':'output_token_budget_exhausted'})
