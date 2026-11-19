import json

from tools import live_evaluation_data as evaluation
from tools import baseline_data
from tools.render_bench_scale_charts import score_specs
from tools.watch_dashboard_publication import chart_evidence


def test_partial_cost_pool_cannot_price_a_full_benchmark_score(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'read', lambda _: {})
    monkeypatch.setattr(evaluation, 'physics_score', lambda *a: {'scope':198, 'metrics':{'Verified':0.5}})
    for name in ['robo_score', 'pcve_score', 'phygen_score']:
        monkeypatch.setattr(evaluation, name, lambda *a: None)
    monkeypatch.setattr(baseline_data, 'video_baselines', lambda _: [])
    cost = {'mean_cost_usd':1, 'accepted_cases':197, 'cache_usage_complete_cases':197}
    assert not score_specs(tmp_path, {'physics_iq':{'gpt61-sol':cost}})['physics_iq']['models']
    cost.update(accepted_cases=198, cache_usage_complete_cases=198)
    assert score_specs(tmp_path, {'physics_iq':{'gpt61-sol':cost}})['physics_iq']['models']


def test_phygen_plot_excludes_a_different_judge_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'read', lambda _: {})
    for name in ['physics_score', 'robo_score', 'pcve_score', 'phygen_score']:
        monkeypatch.setattr(evaluation, name, lambda *a: None)
    baselines = [dict(label='different judge',price={'usd_per_video':1},scores={
        'phygenbench':{'value':0.2,'kind':'published_reference'}}),
        dict(label='matched judge',price={'usd_per_video':1},scores={
        'phygenbench':{'value':0.7,'kind':'matched_rerun'}})]
    monkeypatch.setattr(baseline_data, 'video_baselines', lambda _: baselines)
    spec=score_specs(tmp_path,{})['phygenbench']
    assert spec['baselines']==[] and spec['excluded_baselines']==['different judge']
    assert spec['matched_baselines'][0][0]=='matched judge'


def test_chart_refresh_ignores_clock_ticks_but_tracks_coverage(tmp_path, monkeypatch):
    source=tmp_path/'publication/pixelworld';source.mkdir(parents=True)
    monkeypatch.setattr(baseline_data, 'video_baselines', lambda _: [])
    identity=dict(suite='physics_iq_720p24fps',model_key='model',track='vision-agent')
    (source/'evaluation.json').write_text(json.dumps({'cohorts':[
        {**identity,'full_scope_scored':True,'score':{'metrics':{'Verified':0.5}}}]}))
    group={**identity,'cached_input_tokens_measured_cases':197}
    (source/'catalogue.json').write_text(json.dumps({'updated_utc':'first','groups':[group]}))
    first=chart_evidence(source)
    (source/'catalogue.json').write_text(json.dumps({'updated_utc':'second','groups':[group]}))
    assert chart_evidence(source)==first
    group['cached_input_tokens_measured_cases']=198
    (source/'catalogue.json').write_text(json.dumps({'groups':[group]}))
    assert chart_evidence(source)!=first
