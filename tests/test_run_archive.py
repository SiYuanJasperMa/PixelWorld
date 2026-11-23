import json
import tarfile

import numpy as np
import pytest

from pixelworld.run_archive import archive_closed_attempt, archive_if_configured
from pixelworld.util import sha256


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


def prepare_prune_case(tmp_path):
    run = completed_run(tmp_path)
    tensor = np.arange(8, dtype=np.uint32).reshape(2, 2, 2)
    workspace = run/'workspace'
    np.savez_compressed(workspace/'pixels.npz', pixels=tensor)
    np.save(workspace/'pixels.packing.npy', tensor)
    manifest = run/'run_manifest.json'
    data = json.loads(manifest.read_text())
    data.update(validation={'pass': True, 'png_tensor_exact': True},
                pixels_sha256=sha256(workspace/'pixels.npz'))
    manifest.write_text(json.dumps(data))
    proxy = tmp_path/'proxy'; proxy.mkdir()
    (proxy/'resp_test.input.json').write_text('{}')
    (proxy/'resp_test.response.json').write_text('{}')
    return run, workspace, proxy


def test_pruning_requires_verified_duplicates(tmp_path):
    run, workspace, proxy = prepare_prune_case(tmp_path)
    partial = workspace/'pixels.npz.partial'; partial.write_bytes(b'different interrupted draft')
    root = tmp_path/'archive'
    receipt = archive_closed_attempt(run,root,benchmark='physics_iq',proxy_logs=proxy,prune_reproducible=True)
    assert receipt['pruned_source_files'] == 1
    assert receipt['pruning'][0]['path'] == 'workspace/pixels.packing.npy'
    assert not (workspace/'pixels.packing.npy').exists()
    assert partial.read_bytes() == b'different interrupted draft'
    assert sha256(workspace/'pixels.npz') == receipt['members']['workspace/pixels.npz']
    assert archive_closed_attempt(run,root,benchmark='physics_iq',proxy_logs=proxy,prune_reproducible=True) == receipt


def test_missing_exact_request_body_preserves_scratch(tmp_path):
    run, workspace, proxy = prepare_prune_case(tmp_path)
    (proxy/'resp_test.input.json').rename(proxy/'resp_test.native.json')
    receipt = archive_closed_attempt(run,tmp_path/'archive',benchmark='physics_iq',proxy_logs=proxy,prune_reproducible=True)
    assert receipt['pruned_source_files'] == 0
    assert (workspace/'pixels.packing.npy').is_file()


def test_rbench_evaluation_attachment_matches_exact_generation_revision(tmp_path):
    from tools.archive_rbench_scores import GPT61_JUDGE, PROTOCOL, attach, index_receipts

    run = completed_run(tmp_path)
    root = tmp_path/'archive'
    result = archive_closed_attempt(run, root, benchmark='rbench')
    report = tmp_path/'0001.json'
    row = {'judge': GPT61_JUDGE, 'protocol': PROTOCOL, 'model': 'sample-model',
           'case': '0001', 'source_run': str(run), 'generation_attempt': 1,
           'source_manifest_sha256': 'wrong-revision', 'score': 0.5}
    report.write_text(json.dumps(row))
    index = index_receipts(root)
    assert attach(report, index)['status'] == 'no_unique_exact_archive'
    row['source_manifest_sha256'] = result['members']['run_manifest.json']
    report.write_text(json.dumps(row))
    seal = attach(report, index)
    assert seal['status'] == 'verified' and seal['score_sha256'] == sha256(report)
    path = root/'rbench/sample-model/run_0001/evaluations'/PROTOCOL/'result.json'
    assert path.read_bytes() == report.read_bytes()
    assert attach(report, index) == seal
    row['score'] = 0.6
    report.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='different score record'):
        attach(report, index)
