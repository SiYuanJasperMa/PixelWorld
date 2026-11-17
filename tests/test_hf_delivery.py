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
