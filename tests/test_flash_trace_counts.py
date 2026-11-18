import json
from tools.analyze_flash_rollouts import recorded_trace, paired_quality, paired_economics, build, refinement_summary
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


def test_custom_patch_calls_and_returns_are_counted_once(tmp_path):
    path=tmp_path/'rollout.jsonl'
    call={'type':'response_item','payload':{'type':'custom_tool_call','name':'apply_patch',
          'call_id':'patch','input':'*** Begin Patch\n*** End Patch'}}
    result={'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'patch','output':'Success'}}
    path.write_text('\n'.join(json.dumps(x)for x in [call,call,result]))
    trace=recorded_trace(path)
    assert trace['calls']['apply_patch']==1 and trace['tool_text_bytes']==7


def test_partial_cost_comparison_uses_same_cases_and_preserves_failed_spend():
    def row(cost,accepted=True):return {'input':cost*100,'output':cost*10,'duration_seconds':cost,
        'cost_normalized70':cost,'protocol_delivery':accepted}
    reference={'a':row(1),'b':row(1),'unmatched':row(100)}
    current={'a':row(.5),'b':row(.5,False)}
    result=paired_economics(reference,current)
    assert result['paired_closed_cases']==2
    assert result['reference']['cost_per_delivery_normalized70']==1
    assert result['candidate']['mean_attempt_cost_normalized70']==.5
    assert result['candidate']['cost_per_delivery_normalized70']==1
    assert result['cost_per_delivery_normalized70_relative_delta']==0


def test_unknown_failed_attempt_usage_prevents_complete_paired_cost_claim(tmp_path):
    (tmp_path/'experiment_manifest.json').write_text(json.dumps({'arms':['baseline','api'],'case_ids':['0001']}))
    for arm,attempt,status,usage in [('baseline',1,'complete',{'status':'recorded','input_tokens':100,'output_tokens':10}),
        ('api',1,'failed',{'status':'partial'}),('api',2,'complete',{'status':'recorded','input_tokens':200,'output_tokens':20})]:
        run=tmp_path/f'generations/{arm}/gpt56-luna/vision-agent/case_0001_attempt_{attempt:02d}';run.mkdir(parents=True)
        (run/'run_manifest.json').write_text(json.dumps({'case_id':'0001','status':status,'token_usage':usage,
            'validation':{'pass':status=='complete','frames':120,'png_tensor_exact':True,'frame0_exact':True}}))
    result=next(x for x in build(tmp_path,tmp_path/'report.json')['arms']if x['key']=='api')
    assert result['paired_economics'] is None
    assert result['cost_per_protocol_delivery_normalized70'] is None


def test_refinement_summary_rejects_stale_metrics_pixels_and_sampling(tmp_path):
    folder = tmp_path / 'quality'
    folder.mkdir()
    row = {'case': '0001', 'status': 'scored', 'sampling': 'native24FPS/120frames',
           'first_pixels_sha256': 'first', 'final_pixels_sha256': 'final',
           'first_v1': .2, 'first_v2': .3, 'final_v1': .25, 'final_v2': .35}
    path = folder / 'refinement_native24.json'
    path.write_text(json.dumps({'per_case': [row]}))
    quality = {'scoring_fps': 24, 'scoring_frames': 120, 'per_case': [
        {'arm': 'baseline', 'case': '0001', 'pixels_sha256': 'final', 'v1': .25, 'v2': .35}]}
    result = refinement_summary(tmp_path, quality, {})
    assert result['matched_cases'] == 1 and result['changed_tensor_cases'] == 1
    assert not result['causal_claim']
    assert result['paired']['v1']['ci95'] is None
    quality['scoring_fps'] = 4
    assert refinement_summary(tmp_path, quality, {}) is None
    quality['scoring_fps'] = 24
    for field, value in [('final_pixels_sha256', 'stale'), ('final_v1', .26),
                         ('sampling', 'development4FPS')]:
        path.write_text(json.dumps({'per_case': [{**row, field: value}]}))
        result = refinement_summary(tmp_path, quality, {})
        assert result['matched_cases'] == 0 and result['excluded_cases'] == ['0001']


def test_infra_retry_counts_one_case_but_preserves_unmeasured_spending(tmp_path):
    (tmp_path/'experiment_manifest.json').write_text(json.dumps({'arms':['baseline','api'],'case_ids':['0001','0002']}))
    for arm, attempt, state, usage, validation in [
        ('baseline',1,'complete',{'status':'recorded','input_tokens':100,'output_tokens':10},{'pass':True}),
        ('api',1,'failed',{'status':'partial'},{'pass':False,'infra_error':'storage'}),
        ('api',2,'complete',{'status':'recorded','input_tokens':200,'output_tokens':20},{'pass':True})]:
        run=tmp_path/f'generations/{arm}/gpt56-luna/vision-agent/case_0001_attempt_{attempt:02d}'
        run.mkdir(parents=True)
        (run/'run_manifest.json').write_text(json.dumps({'case_id':'0001','attempt':attempt,'status':state,
            'token_usage':usage,'validation':{**validation,'frames':120,'png_tensor_exact':True,'frame0_exact':True}}))
    result=next(x for x in build(tmp_path,tmp_path/'report.json')['arms']if x['key']=='api')
    assert result['counts']=={'complete':1}
    assert result['attempt_counts']=={'failed':1,'complete':1}
    assert result['pending']==1 and result['valid_rgb']==1 and result['original_protocol_pass']==1
    assert result['infrastructure_interruptions']==1
    assert result['unknown_prior_usage_cases']==['0001']
    assert not result['cost_usage_complete_for_closed_attempts']
    assert result['paired_economics'] is None


def test_explicit_arm_roots_reuse_baseline_without_copying_or_generating(tmp_path):
    reference=tmp_path/'original_reference';cohort=tmp_path/'study';cohort.mkdir()
    (cohort/'experiment_manifest.json').write_text(json.dumps({'arms':['baseline','submit'],
        'case_ids':['0001'],'model_key':'other','model':'Other model',
        'arm_folders':{'baseline':str(reference),'submit':'actual_runs'}}))
    for folder,inp in [(reference,100),(cohort/'actual_runs',50)]:
        run=folder/'other/vision-agent/case_0001_attempt_01';run.mkdir(parents=True)
        (run/'run_manifest.json').write_text(json.dumps({'case_id':'0001','attempt':1,'status':'complete',
            'validation':{'pass':True},'token_usage':{'status':'recorded','input_tokens':inp,'output_tokens':10}}))
    report=build(cohort,cohort/'report.json')
    assert [x['key'] for x in report['arms']]==['baseline','submit']
    assert all(x['model']=='Other model' for x in report['arms'])
    assert report['arms'][1]['paired_economics']['candidate']['mean_input']==50
    assert not (cohort/'generations/baseline').exists()


def test_only_proven_preclient_rejection_is_zero_sampling_not_missing_usage(tmp_path):
    (tmp_path/'experiment_manifest.json').write_text(json.dumps({'arms':['baseline','submit'],
        'case_ids':['0001'],'arm_folders':{'submit':'actual'}}))
    values=[('generations/baseline',1,'complete',{'status':'recorded','input_tokens':100,'output_tokens':10}),
            ('actual',1,'failed',{'status':'missing'}),
            ('actual',2,'complete',{'status':'recorded','input_tokens':50,'output_tokens':5})]
    for folder,attempt,status,usage in values:
        run=tmp_path/f'{folder}/gpt56-luna/vision-agent/case_0001_attempt_{attempt:02d}'
        run.mkdir(parents=True)
        record={'case_id':'0001','attempt':attempt,'status':status,'token_usage':usage,
                'validation':{'pass':status=='complete'}}
        if status=='failed':
            record.update(cli_backend='codex',codex_version='codex-cli 0.169.0',
                stop_reason='local_cli_configuration_failure',command=['codex','--trust','--ignore-user-config'])
            logs=run/'logs';logs.mkdir();(logs/'events.jsonl').write_text('')
            (logs/'stderr.log').write_text('Error: --trust cannot be combined with --ignore-user-config: saved project trust decisions must be checked')
        (run/'run_manifest.json').write_text(json.dumps(record))
    report=next(x for x in build(tmp_path,tmp_path/'report.json')['arms']if x['key']=='submit')
    assert report['paired_economics']['paired_closed_cases']==1
    assert report['usage_measured_cases']==1
    assert len(report['proven_unlaunched_attempts'])==1
    assert report['cost_usage_complete_for_closed_attempts']
    logs=tmp_path/'actual/gpt56-luna/vision-agent/case_0001_attempt_01/logs'
    (logs/'events.jsonl').write_text('{"type":"turn.started"}\n')
    report=next(x for x in build(tmp_path,tmp_path/'report.json')['arms']if x['key']=='submit')
    assert not report['cost_usage_complete_for_closed_attempts']
    assert report['paired_economics'] is None
