import json
from pathlib import Path
import subprocess
import sys

from tools.archive_recent_registered_runs import archive_recent, discover_registered, registered_configs


def fixture(tmp_path):
    root = tmp_path / 'runs'
    run = root / 'm/vision-agent/case_0001_attempt_01'
    (run / 'recording').mkdir(parents=True)
    (run / 'workspace').mkdir()
    (run / 'workspace/pixels.npz.partial').write_bytes(b'keep staging draft')
    manifest = {'status': 'complete', 'started_utc': '2026-11-24T10:00:00', 'protocol': 'p',
                'model_key': 'm', 'track': 'vision-agent', 'case_id': '0001', 'attempt': 1,
                'provider': {'base_url': 'http://127.0.0.1:9588/v1'}}
    (run / 'run_manifest.json').write_text(json.dumps(manifest))
    (run / 'recording/api_usage.jsonl').write_text(json.dumps({'response_id': 'resp_exact'}) + '\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'runs_root': str(root), 'case_ids': ['0001'], 'protocol': 'p',
                                 'models': [{'key': 'm', 'tracks': ['vision-agent']}]}))
    proxy = tmp_path / 'proxy'; proxy.mkdir()
    (proxy / 'resp_exact.input.json').write_bytes(b'exact request bytes')
    (proxy / 'resp_exact.response.json').write_bytes(b'exact response bytes')
    (proxy / 'local.key').write_bytes(b'must never enter archive')
    routes = {'http://127.0.0.1:9588/v1': proxy}
    return run, config, routes


def test_archives_only_registered_recent_closed_attempts_and_preserves_source(tmp_path, monkeypatch):
    run, config, routes = fixture(tmp_path)
    original = (run / 'run_manifest.json').read_bytes()
    excluded = run.parent / 'case_unregistered_attempt_01'; excluded.mkdir()
    m = json.loads(original); m['case_id'] = 'unregistered'
    (excluded / 'run_manifest.json').write_text(json.dumps(m))
    monkeypatch.setattr(Path, 'rglob', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('broad scan')))
    configs = {config: 'legacy-physics'}
    assert len(list(discover_registered(configs))) == 1
    root = tmp_path / 'private'
    assert archive_recent(configs, root, routes)['counts'] == {'eligible': 1}
    assert not root.exists()
    report = archive_recent(configs, root, routes, apply=True)
    assert report['counts'] == {'verified': 1}
    assert report['items'][0]['wire_pair_count'] == 1
    assert (run / 'run_manifest.json').read_bytes() == original
    assert (run / 'workspace/pixels.npz.partial').is_file()
    receipt = json.loads((root / 'legacy-physics/m/run_0001/verified.json').read_text())
    assert not any('local.key' in name for name in receipt['members'])
    assert archive_recent(configs, root, routes, apply=True)['counts'] == {'already_verified': 1}


def test_respects_closed_window_exact_route_and_limit(tmp_path):
    run, config, routes = fixture(tmp_path)
    assert archive_recent({config: 'p'}, tmp_path / 'archive', {})['counts'] == {'wire_route_unavailable_artifacts_retained': 1, 'eligible': 1}
    path = run / 'run_manifest.json'; m = json.loads(path.read_text())
    m['status'] = 'running'; path.write_text(json.dumps(m))
    assert archive_recent({config: 'p'}, tmp_path / 'archive', routes)['counts'] == {'active_or_unknown': 1}
    m['status'] = 'failed'; m['started_utc'] = '2026-11-23T00:00:00'; path.write_text(json.dumps(m))
    assert archive_recent({config: 'p'}, tmp_path / 'archive', routes)['counts'] == {'before_window': 1}
    m['completed_utc'] = '2026-11-24T10:00:00'; path.write_text(json.dumps(m))
    assert archive_recent({config: 'p'}, tmp_path / 'archive', routes)['counts'] == {'eligible': 1}
    m['started_utc'] = '2026-11-24T10:00:00'; path.write_text(json.dumps(m))
    second = run.parent / 'case_0001_attempt_02'; second.mkdir(); m['attempt'] = 2
    (second / 'run_manifest.json').write_text(json.dumps(m))
    report = archive_recent({config: 'p'}, tmp_path / 'archive', routes, limit=1)
    assert report['counts'] == {'eligible': 1, 'scan_limit_reached': 1}
    assert not report['registered_scope_scan_complete']


def test_registries_follow_explicit_config_paths_and_job_scopes(tmp_path):
    _, config, _ = fixture(tmp_path)
    registry = tmp_path / 'launched.json'
    registry.write_text(json.dumps({'legacy-physics': {'command': [sys.executable, '-m', 'pixelworld.cli',
                   'matrix', 'run', '--config', str(config)]}, 'retry-only': {'command': ['other', '--config', '/missing']}}))
    assert registered_configs(registries=[registry]) == {config: 'legacy-physics'}
    queue = tmp_path / 'queue.json'; queue.write_text(json.dumps([{'name': 'pcve_text_analysis', 'config': str(config)}]))
    assert registered_configs(registries=[queue]) == {config: 'pcve_text_analysis'}
    assert registered_configs(registries=[registry, queue], overrides={config: 'approved-scope'}) == {config: 'approved-scope'}


def test_existing_source_revision_is_preserved_and_new_copy_is_numbered(tmp_path):
    run, config, routes = fixture(tmp_path)
    root = tmp_path / 'archive'
    archive_recent({config: 'p'}, root, routes, apply=True)
    receipt = root / 'p/m/run_0001/verified.json'; original = receipt.read_bytes()
    path = run / 'run_manifest.json'; path.write_text(path.read_text() + '\n')
    result = archive_recent({config: 'p'}, root, routes, apply=True)
    assert result['counts'] == {'verified': 1}
    assert result['items'][0]['run_number'] == 2
    assert receipt.read_bytes() == original


def test_watch_records_pid_start_marker_and_enforces_single_worker_lock(tmp_path):
    _, config, routes = fixture(tmp_path)
    url, proxy = next(iter(routes.items()))
    report = tmp_path / 'report.json'
    command = [sys.executable, '-m', 'tools.archive_recent_registered_runs', '--config', str(config),
               '--archive-root', str(tmp_path / 'archive'), '--proxy-route', f'{url}={proxy}',
               '--report', str(report), '--watch', '--poll-seconds', '120']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready['ready'] and ready['worker']['pid'] == process.pid
        assert ready['worker']['pid_start_marker']
        second = subprocess.run(command, capture_output=True, text=True, timeout=5)
        assert second.returncode != 0 and 'already owns' in second.stderr
    finally:
        process.terminate(); process.communicate(timeout=5)
