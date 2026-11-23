import json
import tarfile

import pytest

from pixelworld.run_archive import archive_closed_attempt, archive_if_configured


def completed_run(tmp_path):
    run = tmp_path / 'runs' / 'case_0001_attempt_01'
    (run/'workspace').mkdir(parents=True)
    (run/'recording').mkdir()
    (run/'workspace/pixels.npz').write_bytes(b'example tensor')
    (run/'run_manifest.json').write_text(json.dumps({
        'status':'complete','model_key':'sample-model','track':'vision-agent','case_id':'0001','attempt':1,
    }))
    (run/'recording/api_usage.jsonl').write_text(json.dumps({'response_id':'resp_test'})+'\n')
    return run


def test_archive_preserves_raw_pair_and_source(tmp_path):
    run = completed_run(tmp_path)
    proxy = tmp_path/'proxy'; proxy.mkdir()
    (proxy/'resp_test.input.json').write_bytes(b'{"test":"input"}')
    (proxy/'resp_test.response.json').write_bytes(b'{"test":"output"}')
    (proxy/'local.key').write_text('never archive a credential')
    root = tmp_path/'verified'
    receipt = archive_closed_attempt(run,root,benchmark='physics_iq',proxy_logs=proxy)
    assert receipt['run_number'] == 1 and receipt['wire_pair_count'] == 1
    assert receipt['pruned_source_files'] == 0
    with tarfile.open(root/'physics_iq/sample-model/run_0001/artifacts.tar.gz') as tar:
        assert set(tar.getnames()) == set(receipt['members'])
        assert not any('local.key' in k for k in tar.getnames())
        assert tar.extractfile('api_originals/resp_test/response.json').read() == b'{"test":"output"}'
    assert (run/'workspace/pixels.npz').is_file()
    assert archive_closed_attempt(run,root,benchmark='physics_iq',proxy_logs=proxy) == receipt


def test_archive_reports_missing_original_and_ignores_symlinks(tmp_path, monkeypatch):
    run = completed_run(tmp_path)
    key = tmp_path/'key'; key.write_text('private')
    (run/'recording/leak.json').symlink_to(key)
    root = tmp_path/'verified'
    monkeypatch.setenv('PIXELWORLD_VERIFIED_ARCHIVE_ROOT',str(root))
    archive_if_configured(run,benchmark='physics_iq')
    status = json.loads((run/'verified_archive_status.json').read_text())
    assert status['status'] == 'verified' and status['known_requests'] == 1 and status['wire_pair_count'] == 0
    receipt = json.loads((root/'physics_iq/sample-model/run_0001/verified.json').read_text())
    assert 'recording/leak.json' not in receipt['members']
    assert receipt['known_request_roles'] == {'resp_test': []}


def test_archive_refuses_active_run(tmp_path):
    run = completed_run(tmp_path)
    manifest = run/'run_manifest.json'
    d=json.loads(manifest.read_text());d['status']='running';manifest.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='active'):
        archive_closed_attempt(run,tmp_path/'archive',benchmark='physics_iq')
