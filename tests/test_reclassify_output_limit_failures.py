import json

import pytest

from pixelworld.retries import infrastructure_failure
from tools.reclassify_output_limit_failures import BACKUP, evidence, reclassify


def fixture(tmp_path):
    run, proxy = tmp_path/'run', tmp_path/'proxy'
    (run/'recording').mkdir(parents=True)
    (run/'logs').mkdir()
    proxy.mkdir()
    m = {'status': 'failed', 'output_token_budget': 1000000,
         'validation': {'pass': False, 'frames': 0}, 'token_usage': {'output_tokens': 32777}}
    (run/'run_manifest.json').write_text(json.dumps(m))
    (run/'recording/api_usage.jsonl').write_text(json.dumps({
        'response_id': 'resp_verified', 'usage': {'output_tokens': 32000}})+'\n')
    for role, data in [('input', {'model': 'pinned-model'}), ('native', {'max_tokens': 32000}),
                       ('response', {'status':'completed'}),
                       ('events', [{'event': 'token_usage', 'data': {'completion_tokens': 32000}},
                                   {'event': 'done', 'data': {'finish_reason': 'length'}}])]:
        (proxy/f'resp_verified.{role}.json').write_text(json.dumps(data))
    return run, proxy


def test_preserves_original_manifest_and_exempts_only_proven_proxy_failure(tmp_path):
    run, proxy = fixture(tmp_path)
    original = (run/'run_manifest.json').read_bytes()
    assert reclassify(run, proxy)['eligible']
    assert not (run/BACKUP).exists()
    assert reclassify(run, proxy, apply=True)['applied']
    assert (run/BACKUP).read_bytes() == original
    result = json.loads((run/'run_manifest.json').read_text())
    assert result['status'] == 'failed' and result['validation']['pass'] is False
    assert infrastructure_failure(result)
    assert not reclassify(run, proxy, apply=True)['eligible']


@pytest.mark.parametrize('change', [
    {'status': 'complete'}, {'output_token_budget': 32000},
    {'stop_reason': 'output_token_budget_exhausted'},
    {'token_usage': {'output_tokens': 1000000}},
])
def test_does_not_relax_real_model_budget_or_success(tmp_path, change):
    run, proxy = fixture(tmp_path)
    m = json.loads((run/'run_manifest.json').read_text()); m.update(change)
    (run/'run_manifest.json').write_text(json.dumps(m))
    assert evidence(run, proxy) is None


def test_normal_stop_and_explicit_client_cap_do_not_qualify(tmp_path):
    run, proxy = fixture(tmp_path)
    path = proxy/'resp_verified.events.json'
    es = json.loads(path.read_text()); es[-1]['data']['finish_reason'] = 'stop'
    path.write_text(json.dumps(es))
    assert evidence(run, proxy) is None
    es[-1]['data']['finish_reason'] = 'length'; path.write_text(json.dumps(es))
    (proxy/'resp_verified.input.json').write_text('{"max_output_tokens":32000}')
    assert evidence(run, proxy) is None


def test_correctly_reported_upstream_limit_is_not_reclassified_as_adapter_failure(tmp_path):
    run, proxy = fixture(tmp_path)
    (proxy/'resp_verified.response.json').write_text('{"status":"incomplete"}')
    assert evidence(run, proxy) is None
