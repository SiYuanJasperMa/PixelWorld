import json
from tools.analyze_flash_rollouts import recorded_trace, paired_quality, build
import pytest


def test_image_attempt_and_actual_delivery_are_distinct(tmp_path):
    path = tmp_path / 'rollout.jsonl'
    events = [
        {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'view_image',
            'call_id': 'a', 'arguments': '{"path":"first_frame.png"}'}},
        {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'a',
            'output': 'bundled bubblewrap digest mismatch'}},
        {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'view_image',
            'call_id': 'b', 'arguments': '{"path":"inspection.png"}'}},
        {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'b',
            'output': [{'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='}]}},
    ]
    path.write_text('\n'.join(json.dumps(e) for e in events))
    result = recorded_trace(path)
    assert result['calls']['view_image'] == 2
    assert result['image_results'] == {'helper_digest_failures': 1, 'returned_image_messages': 1}


def test_duplicate_cumulative_usage_event_does_not_add_a_request(tmp_path):
    path = tmp_path / 'rollout.jsonl'
    event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'total_token_usage': {'input_tokens': 100, 'output_tokens': 5, 'cached_input_tokens': 0},
        'last_token_usage': {'input_tokens': 100, 'output_tokens': 5}}}}
    path.write_text(json.dumps(event)+'\n'+json.dumps(event)+'\n')
    result = recorded_trace(path)
    assert result['model_requests'] == 1
    assert result['rollout_input_tokens'] == 100


def test_single_case_smoke_has_no_sampling_interval_or_preservation_claim():
    reference={'0047':{'v1':.25,'v2':.1}}
    candidate={'0047':{'v1':.26,'v2':.11}}
    result=paired_quality(reference,candidate,{'0047':'one-scenario'})
    assert result['paired_cases']==1
    assert result['v1']['ci95'] is None
    assert not result['v1']['development_interval_within_margin']
    assert not result['v2']['development_interval_within_margin']


def test_failed_attempt_spend_is_included_in_cost_per_protocol_delivery(tmp_path):
    (tmp_path/'experiment_manifest.json').write_text(json.dumps({'arms':['baseline']}))
    for index,success in enumerate([True,False],1):
        folder=tmp_path/f'generations/baseline/gpt56-luna/vision-agent/case_{index:04d}_attempt_01'
        folder.mkdir(parents=True)
        (folder/'run_manifest.json').write_text(json.dumps({'status':'complete' if success else 'failed',
            'validation':{'pass':success,'frames':120,'png_tensor_exact':True,'frame0_exact':True},
            'token_usage':{'status':'recorded','input_tokens':100*index,'output_tokens':10*index}}))
    result=build(tmp_path,tmp_path/'report.json')['arms'][0]
    assert result['valid_rgb']==2 and result['original_protocol_pass']==1
    assert result['cost_usage_complete_for_closed_attempts']
    assert result['cost_per_protocol_delivery_normalized70']==pytest.approx(.00261)
    assert result['mean_cost_normalized70']==pytest.approx(.001305)
