import json
import sqlite3

import pytest

from tools.archive_proxy_request_index import request_ids_for_run, update_index


def encoded(rid, thread='thread-a', started=100, model='public-alias', **extra):
    return (json.dumps({'id': rid, 'thread_id': thread, 'started': started,
                       'model': model, 'native_model': 'native-model',
                       'metadata': {'private_payload': 'do not retain'}, **extra}) + '\n').encode()


def paths(tmp_path):
    source = tmp_path / 'proxy/requests.jsonl'
    source.parent.mkdir()
    database = tmp_path / 'private/index.sqlite'
    return source, database


def make_run(tmp_path, manifest=None):
    run = tmp_path / 'run'
    (run / 'logs').mkdir(parents=True)
    (run / 'recording').mkdir()
    (run / 'run_manifest.json').write_text(json.dumps(manifest or {}))
    return run


def test_append_partial_line_and_noop_checkpoint(tmp_path):
    source, db = paths(tmp_path)
    second = encoded('resp_second')
    source.write_bytes(encoded('resp_first') + second[:-4])
    report = update_index(source.parent, db, batch_size=1)
    assert report['indexed_requests'] == 1
    assert report['pending_bytes'] == len(second) - 4
    assert report['offset'] == len(encoded('resp_first'))
    noop = update_index(source, db)
    assert noop['new_lines'] == 0 and noop['bytes_consumed'] == 0
    with source.open('ab') as handle:
        handle.write(second[-4:] + encoded('resp_third'))
    report = update_index(source, db, batch_size=1)
    assert report['indexed_requests'] == 3 and report['new_lines'] == 2
    assert report['pending_bytes'] == 0 and report['reset_reason'] is None
    assert request_ids_for_run(db, {'full_trace': {'thread_id': 'thread-a'}})['request_ids'] == [
        'resp_first', 'resp_second', 'resp_third']
    assert db.stat().st_mode & 0o777 == 0o600
    assert db.with_suffix('.sqlite.lock').stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(db) as connection:
        assert [row[1] for row in connection.execute('PRAGMA table_info(requests)')] == [
            'id', 'thread_id', 'started', 'model', 'native_model']


def test_missing_thread_never_matches_by_time_or_model(tmp_path):
    source, db = paths(tmp_path)
    source.write_bytes(encoded('resp_null', thread=None) + encoded('resp_other', thread='another'))
    update_index(source, db)
    manifest = {'model': 'public-alias', 'started_utc': 90, 'completed_utc': 110}
    result = request_ids_for_run(db, manifest)
    assert result['request_ids'] == []
    assert result['counts']['indexed_without_thread'] == 1
    assert result['counts']['missing_thread_identity'] == 1
    manifest['full_trace'] = {'thread_id': 'thread-a'}
    assert request_ids_for_run(db, manifest)['request_ids'] == []


def test_direct_ledger_and_explicit_thread_union_with_window(tmp_path):
    source, db = paths(tmp_path)
    source.write_bytes(b''.join([
        encoded('resp_match'), encoded('resp_old', started=89), encoded('resp_future', started=111),
        encoded('resp_null', thread=None), encoded('resp_other', thread='another')]))
    update_index(source, db)
    run = make_run(tmp_path, {'model': 'different-manifest-alias', 'started_utc': 90, 'completed_utc': 110,
                              'full_trace': {'thread_id': 'thread-a'}})
    (run / 'recording/api_usage.jsonl').write_text('\n'.join([
        json.dumps({'response_id': 'resp_null'}), json.dumps({'response_id': 'resp_unindexed'}),
        json.dumps({'response_id': '../unsafe'}), 'not-json', json.dumps({'response_id': 'resp_null'})]) + '\n')
    result = request_ids_for_run(db, run)
    assert result['request_ids'] == ['resp_match', 'resp_null', 'resp_unindexed']
    assert result['thread_request_ids'] == ['resp_match']
    assert result['counts']['direct_unindexed'] == 1
    assert result['counts']['invalid_ledger_lines'] == 1
    assert result['counts']['thread_candidates'] == 3
    assert result['counts']['thread_filtered_out'] == 2
    # Direct IDs do not require the index to know a thread or model.
    assert request_ids_for_run(db, {}, run_path=run)['request_ids'] == ['resp_null', 'resp_unindexed']
    manifest = json.loads((run / 'run_manifest.json').read_text())
    assert request_ids_for_run(db, manifest, run_path=run)['request_ids'] == result['request_ids']


@pytest.mark.parametrize('source_name', ['status', 'event'])
def test_thread_identity_from_recorded_status_or_event(tmp_path, source_name):
    source, db = paths(tmp_path)
    source.write_bytes(encoded('resp_found'))
    update_index(source, db)
    run = make_run(tmp_path)
    if source_name == 'status':
        (run / 'logs/full_trace_status.json').write_text(json.dumps({'thread_id': 'thread-a'}))
    else:
        (run / 'logs/events.jsonl').write_text(json.dumps({'type': 'thread.started', 'thread_id': 'thread-a'}) + '\n')
    result = request_ids_for_run(db, run / 'run_manifest.json')
    assert result['thread_id'] == 'thread-a'
    assert result['request_ids'] == ['resp_found']


def test_conflicting_threads_disable_expansion_but_keep_direct_ids(tmp_path):
    source, db = paths(tmp_path)
    source.write_bytes(encoded('resp_found'))
    update_index(source, db)
    run = make_run(tmp_path, {'full_trace': {'thread_id': 'thread-a'}, 'response_ids': ['resp_direct']})
    (run / 'logs/events.jsonl').write_text(json.dumps({'type': 'thread.started', 'thread_id': 'other'}) + '\n')
    result = request_ids_for_run(db, run)
    assert result['thread_id'] is None and result['request_ids'] == ['resp_direct']
    assert result['counts']['ambiguous_thread_identity'] == 1


def test_timestamp_normalization_and_optional_exact_model(tmp_path):
    source, db = paths(tmp_path)
    source.write_bytes(encoded('resp_first', started='2026-11-24T10:00:00Z') +
                       encoded('resp_second', started='2026-11-24T10:00:01+00:00') +
                       encoded('resp_unknown', started=None))
    update_index(source, db)
    manifest = {'full_trace': {'thread_id': 'thread-a'}, 'started_utc': '2026-11-24T10:00:00',
                'completed_utc': '2026-11-24T10:00:00+00:00'}
    assert request_ids_for_run(db, manifest, exact_model='native-model')['request_ids'] == ['resp_first']
    assert request_ids_for_run(db, manifest, exact_model='unrelated')['request_ids'] == []


@pytest.mark.parametrize('replacement', ['truncate', 'inode', 'regrow'])
def test_source_reset_removes_stale_ids(tmp_path, replacement):
    source, db = paths(tmp_path)
    old = encoded('resp_old') + encoded('resp_stale')
    source.write_bytes(old)
    update_index(source, db, batch_size=1)
    new = encoded('resp_new')
    if replacement == 'inode':
        rotated = source.with_suffix('.rotated')
        source.rename(rotated)
        source.write_bytes(new)
        reason = 'inode_changed'
    elif replacement == 'regrow':
        source.write_bytes(new + encoded('resp_another', padding='x' * len(old)))
        reason = 'checkpoint_bytes_changed'
    else:
        source.write_bytes(new)
        reason = 'source_truncated'
    report = update_index(source, db)
    assert report['reset_reason'] == reason
    result = request_ids_for_run(db, {'full_trace': {'thread_id': 'thread-a'}})
    assert 'resp_old' not in result['request_ids'] and 'resp_stale' not in result['request_ids']
    assert 'resp_new' in result['request_ids']


def test_invalid_records_bounded_oversized_line_and_duplicate_ids(tmp_path, monkeypatch):
    import tools.archive_proxy_request_index as index

    monkeypatch.setattr(index, 'MAX_LINE_BYTES', 256)
    source, db = paths(tmp_path)
    source.write_bytes(b'not-json\n' + b'[]\n' + encoded('../wrong') + b'x' * 1024 + b'\n' +
                       encoded('resp_ok') + encoded('resp_ok', thread=None))
    report = update_index(source, db, batch_size=2)
    assert report['new_lines'] == 6 and report['invalid_lines'] == 4
    assert report['indexed_requests'] == 1 and report['indexed_without_thread'] == 1
    assert update_index(source, db)['new_lines'] == 0


def test_source_is_explicit_and_database_cannot_mix_routes(tmp_path):
    source, db = paths(tmp_path)
    source.write_bytes(encoded('resp_ok'))
    update_index(source, db)
    other = tmp_path / 'other/requests.jsonl'
    other.parent.mkdir()
    other.write_bytes(encoded('resp_other'))
    with pytest.raises(ValueError, match='different explicit proxy source'):
        update_index(other, db)
    link = tmp_path / 'linked/requests.jsonl'
    link.parent.mkdir()
    link.symlink_to(source)
    with pytest.raises(ValueError, match='real requests.jsonl'):
        update_index(link, db)
    with pytest.raises(ValueError, match='positive integer'):
        update_index(source, db, batch_size=0)
