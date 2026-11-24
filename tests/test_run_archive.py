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


def test_changed_manifest_creates_revision_and_preserves_original_bytes(tmp_path):
    run = completed_run(tmp_path)
    root = tmp_path/'archive'
    first = archive_closed_attempt(run, root, benchmark='p')
    directory = root/'p/sample-model/run_0001'
    old_receipt = (directory/'verified.json').read_bytes()
    old_archive = (directory/'artifacts.tar.gz').read_bytes()
    manifest = run/'run_manifest.json'
    data = json.loads(manifest.read_text()); data['audit_correction'] = {'reason': 'proven false positive'}
    manifest.write_text(json.dumps(data))
    second = archive_closed_attempt(run, root, benchmark='p')
    assert second['run_number'] == 2 and second['revision']['previous_run_number'] == 1
    assert first['members']['run_manifest.json'] != second['members']['run_manifest.json']
    assert (directory/'verified.json').read_bytes() == old_receipt
    assert (directory/'artifacts.tar.gz').read_bytes() == old_archive
    assert archive_closed_attempt(run, root, benchmark='p') == second


def test_late_wire_configuration_and_missing_ids_are_honest_revisions(tmp_path):
    run = completed_run(tmp_path)
    root = tmp_path/'archive'; proxy = tmp_path/'proxy'; proxy.mkdir()
    config = tmp_path/'config.json'; config.write_text('{"max_concurrency": 1}')
    extra = {'configuration/registry_config.json': config}
    first = archive_closed_attempt(run, root, benchmark='p', proxy_logs=proxy, extra_files=extra)
    assert first['wire_pair_count'] == 0
    (proxy/'resp_test.input.json').write_text('{}')
    (proxy/'resp_test.response.json').write_text('{}')
    second = archive_closed_attempt(run, root, benchmark='p', proxy_logs=proxy, extra_files=extra)
    assert second['run_number'] == 2 and second['wire_pair_count'] == 1
    config.write_text('{"max_concurrency": 2}')
    third = archive_closed_attempt(run, root, benchmark='p', proxy_logs=proxy, extra_files=extra,
                                   request_ids=['resp_stillMissing'])
    assert third['run_number'] == 3
    assert third['known_request_roles']['resp_stillMissing'] == []
    assert 'configuration/registry_config.json' in third['members']


def test_score_lookup_prefers_latest_exact_manifest_revision(tmp_path):
    from tools.archive_rbench_scores import index_receipts
    run = completed_run(tmp_path); root = tmp_path/'archive'
    first = archive_closed_attempt(run, root, benchmark='p')
    config = tmp_path/'config.json'; config.write_text('{}')
    second = archive_closed_attempt(run, root, benchmark='p', extra_files={'configuration/config.json':config})
    lookup = index_receipts(root)
    assert lookup[(str(run), first['members']['run_manifest.json'])][0][1]['run_number'] == second['run_number'] == 2


def test_late_prune_independent_readback_preserves_original_receipt(tmp_path):
    run, workspace, proxy = prepare_prune_case(tmp_path)
    root = tmp_path/'archive'
    archive_closed_attempt(run, root, benchmark='p', proxy_logs=proxy)
    receipt = root/'p/sample-model/run_0001/verified.json'; original = receipt.read_bytes()
    result = archive_closed_attempt(run, root, benchmark='p', proxy_logs=proxy, prune_reproducible=True)
    assert result['pruned_source_files'] == 1
    assert receipt.read_bytes() == original
    assert not (workspace/'pixels.packing.npy').exists()
    assert (workspace/'pixels.npz').is_file()
