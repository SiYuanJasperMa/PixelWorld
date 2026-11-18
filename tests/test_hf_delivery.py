import json
from tools.prepare_hf_delivery import json_copy, sanitize


def test_public_copy_redacts_secrets_and_keeps_original_trace(tmp_path):
    original = tmp_path / 'original.json'
    record = {'events': [{'command': 'python /mnt/private/generate.py',
        'text': 'Bearer secretvalue'}], 'api_key': 'secretvalue',
        'input_tokens': 123, 'output_tokens': 456}
    original.write_text(json.dumps(record))
    target = tmp_path / 'snapshot.json'
    public = json_copy(original, target)
    assert 'secretvalue' not in target.read_text()
    assert '/mnt/private' not in target.read_text()
    assert public['input_tokens'] == 123 and public['output_tokens'] == 456
    assert json.loads(original.read_text()) == record


def test_hf_credential_in_arbitrary_trace_text_is_redacted():
    token = 'hf_' + 'a' * 30
    assert token not in sanitize({'trace': token})['trace']


def test_absolute_path_keys_are_redacted_without_colliding():
    from tools.prepare_hf_delivery import sanitize
    result=sanitize({'/mnt/private/a':'read','/mnt/private/b':'write'})
    assert len(result)==2
    assert '/mnt/' not in str(result)


def test_spilled_trace_blob_reference_tracks_redacted_bytes(tmp_path):
    import hashlib
    from tools.prepare_hf_delivery import public_blobs,trace_names
    original=b'python /mnt/private/generate.py'
    digest=hashlib.sha256(original).hexdigest()
    blob=tmp_path/'logs/rollout-blobs/sha256'/digest[:2]/digest
    blob.parent.mkdir(parents=True);blob.write_bytes(original)
    image=tmp_path/'logs/tool_images/read.png';image.parent.mkdir();image.write_bytes(b'png')
    records,mapping=public_blobs(tmp_path)
    name,content=next(iter(records.items()))
    assert '/mnt/' not in content.decode()
    assert name.endswith(hashlib.sha256(content).hexdigest())
    assert len(mapping)==1 and next(iter(mapping.values())).endswith(':'+str(len(content)))
    assert 'logs/tool_images/read.png' in trace_names(tmp_path)
    assert blob.read_bytes()==original


def test_complete_case_archive_contains_actual_tool_images_and_code_versions(tmp_path):
    import json,tarfile
    from tools.prepare_hf_delivery import export_trace_run
    run=tmp_path/'run';(run/'logs/tool_images').mkdir(parents=True)
    (run/'trace/round_001').mkdir(parents=True)
    (run/'run_manifest.json').write_text(json.dumps({'case':'x','workspace':'/mnt/private/x'}))
    (run/'logs/tool_images/a.png').write_bytes(b'image-result')
    (run/'trace/round_001/generate.py').write_text('print("candidate")')
    target=export_trace_run(run,tmp_path/'archive.tar.gz')
    with tarfile.open(target) as archive:
        names=archive.getnames()
        assert 'logs/tool_images/a.png' in names
        assert 'trace/round_001/generate.py' in names
        assert b'/mnt/private' not in archive.extractfile('run_manifest.json').read()


def test_submission_receipts_gateway_records_and_stderr_are_in_full_trace(tmp_path):
    import tarfile
    from tools.prepare_hf_delivery import export_trace_run
    run=tmp_path/'run';(run/'logs/submission_requests').mkdir(parents=True)
    (run/'logs/submission_receipt.json').write_text('{"accepted":true}')
    (run/'logs/submission_boundary.json').write_text('{"pre_upstream":true}')
    (run/'logs/submission_requests/one.json').write_text('{"phase":"completed","usage":{"input_tokens":100}}')
    (run/'logs/stderr.log').write_text('CLI setup failed at /mnt/private/config')
    with tarfile.open(export_trace_run(run,tmp_path/'trace.tar.gz'))as archive:
        assert {'logs/submission_receipt.json','logs/submission_boundary.json',
                'logs/submission_requests/one.json','logs/stderr.log'}<=set(archive.getnames())
        assert b'/mnt/private' not in archive.extractfile('logs/stderr.log').read()
