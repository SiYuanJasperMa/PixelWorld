import json

from tools import live_benchmark_data as live


def record(model='model', started=900, elapsed=50, **extra):
    return dict(model=model, started=started, elapsed=elapsed, http_status=200, **extra)


def test_provider_health_uses_configured_indexes_and_completion_time(tmp_path, monkeypatch):
    monkeypatch.setattr(live.time, 'time', lambda: 1000)
    logs = tmp_path / 'cloud_logs'
    logs.mkdir()
    (logs / 'requests.jsonl').write_text(json.dumps(record()) + '\n')
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'recovered_worker_services.json').write_text(json.dumps({'services': [
        {'command': ['python', 'proxy.py', '--log-dir', str(logs)]},
        {'command': ['python', 'proxy.py', '--log-dir', str(logs)]},
    ]}))
    result = live.provider_health(tmp_path)
    assert result['status'] == 'observed' and result['index_count'] == 1
    assert result['models']['model']['age_seconds'] == 50
    assert result['models']['model']['last_success_utc'].endswith('00:15:50+00:00')
    assert str(logs) not in json.dumps(result)


def test_http_200_stream_failure_is_not_a_success(tmp_path, monkeypatch):
    monkeypatch.setattr(live.time, 'time', lambda: 1000)
    rows = [record('ok'), record('broken', error_type='ReadTimeout'),
            record('disconnected', client_disconnected=True),
            record('no_completion', elapsed=None),
            record('old', started=-10000), record('future', started=1100),
            {'model': 'rate_limited', 'started': 900, 'elapsed': 50, 'http_status': 429}]
    (tmp_path / 'requests.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n')
    assert set(live.provider_health(log_roots=[tmp_path])['models']) == {'ok'}


def test_large_index_tail_ignores_partial_and_malformed_records(tmp_path, monkeypatch):
    monkeypatch.setattr(live.time, 'time', lambda: 1000)
    path = tmp_path / 'requests.jsonl'
    path.write_bytes(b'x' * (3 * 1024 * 1024) + b'\nnot-json\n[]\n' +
                     json.dumps(record()).encode() + b'\n{"partial":')
    result = live.provider_health(log_roots=[tmp_path])
    assert result['models']['model']['age_seconds'] == 50


def test_no_configured_index_remains_unknown(tmp_path):
    result = live.provider_health(tmp_path)
    assert result['status'] == 'unknown' and result['models'] == {}
    assert result['index_count'] == 0
