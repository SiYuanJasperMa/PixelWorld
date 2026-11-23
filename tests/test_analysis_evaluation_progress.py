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
