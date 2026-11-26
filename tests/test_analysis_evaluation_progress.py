import json
import pytest

from tools.build_analysis_dashboard import build


@pytest.mark.parametrize('complete', [50, 49])
def test_analysis_score_requires_current_full_generation_scope(tmp_path, complete):
    bundle = tmp_path / 'bundle'
    web = tmp_path / 'web'
    web.mkdir()
    (bundle / 'configs').mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({'models': [
        {'key':'m', 'display_name':'Model', 'model':'model', 'analysis':True}]}))
    (bundle / 'configs/robojudge_text_analysis.json').write_text('{}')
    root = bundle / 'analysis_generations/robojudge_text'
    root.mkdir(parents=True)
    (root / 'status.json').write_text(json.dumps({'groups': {
        'm/text': {'counts': {'complete':complete, 'exhausted':50-complete}}}}))
    (web / 'evaluation.json').write_text(json.dumps({'cohorts': [
        {'suite':'robojudge_text', 'model_key':'m', 'track':'text', 'phase':'complete',
         'full_scope_scored':True, 'score':{'metrics':{'Mean':3.5}},
         'evaluation_progress':{'completed':50, 'expected':50, 'private_path':'/mnt/private'}}]}))
    result = build(web, bundle)
    condition = result['conditions'][0]
    assessment = condition['models'][0]['evaluation']
    assert condition['primary_metric'] == 'Mean'
    assert assessment['full_scope_scored'] is (complete == 50)
    assert condition['scored_models'] == (1 if complete == 50 else 0)
    if complete == 50:
        assert assessment['metrics'] == {'Mean':3.5}
    else:
        assert assessment['metrics'] is None
        assert assessment['phase'] == 'blocked_current_generation_mismatch'
    assert '/mnt/private' not in json.dumps(result)


def test_harness_preparation_is_visible_without_inflating_running_queue(tmp_path):
    bundle = tmp_path / 'bundle'
    web = tmp_path / 'web'
    web.mkdir()
    (bundle / 'analysis_plans').mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({'models':[
        {'key':'m','display_name':'Model','model':'model','analysis':True}]}))
    (bundle / 'analysis_plans/harness_comparison.json').write_text(json.dumps({'conditions':[
        {'id':'physics_iq_harness_pi','title':'Pi 1.6.0','question':'Harness analysis',
         'cases':198,'track':'vision-agent','model_keys':['m'],'planned':True}]}))
    result = build(web, bundle)
    assert result['totals'] == {'complete':0,'running':0,'pending':0,'exhausted':0,'total':0}
    assert result['planned_total'] == 198
    assert result['conditions'][0]['planned']
    assert result['conditions'][0]['counts']['running'] == 0


def test_paused_harness_counts_documented_infrastructure_retries_as_pending(tmp_path):
    bundle = tmp_path / 'bundle'
    web = tmp_path / 'web'
    web.mkdir()
    (bundle / 'configs').mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({'models': [
        {'key': 'm', 'model': 'model', 'analysis': True}]}))
    (bundle / 'configs/physics_iq_harness_pi_analysis.json').write_text('{}')
    root = bundle / 'analysis_generations/physics_iq_harness_pi'
    root.mkdir(parents=True)
    (root / 'status.json').write_text(json.dumps({'dispatch_paused': True, 'groups': {
        'm/vision-agent': {'counts': {'complete': 1, 'exhausted': 3},
                           'infrastructure_retry_pending': 2}}}))
    result = build(web, bundle)
    condition = result['conditions'][0]
    assert condition['paused'] is True
    assert condition['counts'] == {'complete': 1, 'running': 0, 'pending': 196, 'exhausted': 1}
    assert result['totals'] == {'complete': 1, 'running': 0, 'pending': 196, 'exhausted': 1, 'total': 198}


def test_current_worker_source_is_joined_without_exposing_private_paths(tmp_path):
    bundle=tmp_path/'bundle';web=tmp_path/'web';web.mkdir();(bundle/'configs').mkdir(parents=True)
    (bundle/'bundle.json').write_text(json.dumps({'models':[{'key':'m','model':'model','analysis':True}]}))
    (bundle/'configs/physics_iq_text_analysis.json').write_text('{}')
    worker=tmp_path/'worker';worker.mkdir();cfg=tmp_path/'worker.json'
    cfg.write_text(json.dumps({'runs_root':str(worker),'case_ids':[str(i) for i in range(198)],'models':[{'key':'gpt62-sol','tracks':['text']}]}))
    (worker/'status.json').write_text(json.dumps({'updated_utc':'2026-11-26T00:00:00+00:00','groups':{'gpt62-sol/text':{'counts':{'complete':35,'running':1,'pending':162}}}}))
    (bundle/'analysis_runtime_sources.json').write_text(json.dumps({'model_labels':{'gpt62-sol':'GPT-6.2-Sol'},'sources':[{'condition':'physics_iq_text','model_key':'gpt62-sol','config':str(cfg)}]}))
    result=build(web,bundle);row=next(r for r in result['conditions'][0]['models'] if r['key']=='gpt62-sol')
    assert row['complete']==35 and row['running']==1
    assert result['conditions'][0]['updated_utc']=='2026-11-26T00:00:00+00:00'
    assert str(worker) not in json.dumps(result)


def test_scoped_native_model_only_counts_registered_conditions(tmp_path):
    bundle = tmp_path / 'bundle'
    web = tmp_path / 'web'
    web.mkdir()
    (bundle / 'configs').mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({'models': [
        {'key': 'm', 'model': 'model', 'analysis': True}]}))
    for name in ('physics_iq_text_analysis.json', 'pcve_text_analysis.json'):
        (bundle / 'configs' / name).write_text('{}')
    worker = tmp_path / 'native'
    worker.mkdir()
    config = tmp_path / 'native.json'
    config.write_text(json.dumps({'runs_root': str(worker),
        'case_ids': [str(i) for i in range(198)],
        'models': [{'key': 'native-model', 'tracks': ['text']}]}))
    (worker / 'status.json').write_text(json.dumps({'groups': {
        'native-model/text': {'counts': {'complete': 3, 'running': 1, 'pending': 194}}}}))
    (bundle / 'analysis_runtime_sources.json').write_text(json.dumps({
        'model_labels': {'native-model': 'Native model'},
        'scoped_model_keys': ['native-model'], 'sources': [{
            'condition': 'physics_iq_text', 'model_key': 'native-model', 'config': str(config)}]}))
    result = build(web, bundle)
    physics = next(c for c in result['conditions'] if c['id'] == 'physics_iq_text')
    pcve = next(c for c in result['conditions'] if c['id'] == 'pcve_text')
    assert physics['total'] == 396
    assert physics['counts']['complete'] == 3
    assert pcve['total'] == 129
    assert [m['key'] for m in pcve['models']] == ['m']
    assert result['totals']['total'] == 525


@pytest.mark.parametrize('paused_models, expected', [(['native'], False), (['native', 'active'], True)])
def test_one_paused_model_does_not_pause_a_running_condition(tmp_path, paused_models, expected):
    bundle = tmp_path / 'bundle'
    web = tmp_path / 'web'
    web.mkdir()
    (bundle / 'configs').mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({'models': [
        {'key': key, 'model': key, 'analysis': True} for key in ('native', 'active')]}))
    (bundle / 'configs/rbench_text_analysis.json').write_text('{}')
    (bundle / 'configs/scheduling_limits.json').write_text(json.dumps({
        'paused_models_by_config': {'rbench_text_analysis.json': paused_models}}))
    runs = bundle / 'analysis_generations/rbench_text'
    runs.mkdir(parents=True)
    (runs / 'status.json').write_text(json.dumps({'groups': {
        'native/text': {'counts': {'pending': 50}},
        'active/text': {'counts': {'complete': 10, 'running': 2, 'pending': 38}}}}))
    condition = next(c for c in build(web, bundle)['conditions'] if c['id'] == 'rbench_text')
    assert condition['paused'] is expected
