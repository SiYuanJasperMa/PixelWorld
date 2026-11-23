import json
import time
import pytest
from tools import live_evaluation_data as live


def physics_score():
    return {'inputs':198,'upstream_commit':live.PHYSICS_IQ_COMMIT,
            'dataset_revision':live.PHYSICS_IQ_DATASET_REVISION,'model_key':'m','track':'text',
            'verified_score':0.445,'original_formula_score':0.43}


def test_physics_fraction_is_scaled_once_and_partial_or_wrong_revision_is_rejected():
    score=physics_score()
    assert live.physics_score(score,'m','text')['metrics']['Verified']==44.5
    for key,value in [('inputs',197),('dataset_revision','wrong'),('verified_score',float('nan'))]:
        modified={**score,key:value}
        assert live.physics_score(modified,'m','text') is None


def robo_fixture():
    rows=[{'case_id':f'common50_{i:04d}','PA':3,'IA':4}for i in range(50)]
    summary={'complete_shared50':True,'valid_paired_count':50,'items':rows,
             'shared50_means':{'PA':3,'IA':4,'mean':3.5}}
    plan={'scope':{'complete_shared50':True,'prepared_count':50},'model':'m','track':'text',
          'code_revision':live.CODE_REVISION,'checkpoint_revision':live.CHECKPOINT_REVISION,
          'dataset_revision':live.DATASET_REVISION,'tasks_sha256':live.TASKS_SHA256}
    return summary,plan,{'returncode':0}


def test_robojudge_requires_unique_original50_ids_and_matching_means():
    summary,plan,execution=robo_fixture()
    score=live.robo_score(summary,plan,execution,'m','text')
    assert score['metrics']=={'PA':3,'IA':4,'Mean':3.5}
    assert score['table8_comparability_verified'] is False
    summary['items'][49]['case_id']='common50_0000'
    assert live.robo_score(summary,plan,execution,'m','text') is None
    summary,plan,execution=robo_fixture();summary['shared50_means']['PA']=5
    assert live.robo_score(summary,plan,execution,'m','text') is None


def test_current_generation_mismatch_keeps_scores_unclaimed_and_excludes_private_fields(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'COHORTS',{'physics_iq':('Physics-IQ','scheduler',198)})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    gen=tmp_path/'generations/physics_iq';gen.mkdir(parents=True)
    (gen/'status.json').write_text(json.dumps({'groups':{'m/text':{'counts':{'complete':197,'exhausted':1}}}}))
    evalroot=tmp_path/'evaluation/scheduler';cohort=evalroot/'m/text';cohort.mkdir(parents=True)
    (evalroot/'scheduler.json').write_text(json.dumps({'cohorts':{'m/text':{'phase':'complete'}}}))
    score={**physics_score(),'private_path':'/mnt/private/file','api_key':'DO_NOT_PUBLISH'}
    (cohort/'scores.json').write_text(json.dumps(score))
    data=live.collect(tmp_path);row=data['cohorts'][0]
    assert row['score'] is None and row['full_scope_scored'] is False
    assert row['phase']=='blocked_current_generation_mismatch'
    assert '/mnt/private' not in json.dumps(data) and 'DO_NOT_PUBLISH' not in json.dumps(data)


def test_stale_execution_file_does_not_claim_an_active_evaluator(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'COHORTS',{'pcve_smoke':('PCVE','scheduler',129)})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    gen=tmp_path/'generations/pcve_smoke';gen.mkdir(parents=True)
    (gen/'status.json').write_text(json.dumps({'groups':{'m/text':{'counts':{'complete':129}}}}))
    evalroot=tmp_path/'evaluation/scheduler';cohort=evalroot/'m/text';cohort.mkdir(parents=True)
    (evalroot/'scheduler.json').write_text(json.dumps({'cohorts':{'m/text':{'phase':'official_evaluation'}}}))
    (cohort/'execution.json').write_text('{"phase":"official_evaluation"}')
    row=live.collect(tmp_path)['cohorts'][0]
    assert row['phase']=='stale_evaluation' and row['evaluator_active'] is False
    assert row['score'] is None


def test_reference_recovery_shows_preparation_without_a_score(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'COHORTS',{'physics_iq_720p24fps':('Physics-IQ','scheduler',198)})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    gen=tmp_path/'generations/physics_iq_720p24fps';gen.mkdir(parents=True)
    (gen/'status.json').write_text(json.dumps({'groups':{'m/text':{'counts':{'complete':198}}}}))
    evalroot=tmp_path/'evaluation/scheduler';evalroot.mkdir(parents=True)
    (evalroot/'scheduler.json').write_text(json.dumps({'cohorts':{'m/text':{'phase':'failed'}}}))
    state=tmp_path/'state';state.mkdir()
    (state/'main_evaluation_recovery.json').write_text(json.dumps({'phase':'waiting_for_reference_and_previous_export'}))
    row=live.collect(tmp_path)['cohorts'][0]
    assert row['phase']=='preparing_evaluation'
    assert not row['full_scope_scored'] and row['score'] is None


def test_fresh_verified_worker_commands_show_remote_scoring_and_stale_probes_do_not(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'COHORTS',{'physics_iq':('Physics-IQ','scheduler',198)})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    gen=tmp_path/'generations/physics_iq';gen.mkdir(parents=True)
    (gen/'status.json').write_text(json.dumps({'groups':{'m/text':{'counts':{'complete':198}}}}))
    evalroot=tmp_path/'evaluation/scheduler';cohort=evalroot/'m/text';cohort.mkdir(parents=True)
    (evalroot/'scheduler.json').write_text(json.dumps({'pid':123,'cohorts':{'m/text':{'phase':'official_full_evaluation'}}}))
    state=tmp_path/'state';state.mkdir()
    health={'checked_unix':time.time(),'commands':[[456,['python','-m','physiq.run_physics_iq','--input_folders',str(cohort/'videos')]]]}
    (state/'evaluator_worker_health.json').write_text(json.dumps(health))
    assert live.collect(tmp_path)['cohorts'][0]['evaluator_active']
    health['checked_unix']-=600;(state/'evaluator_worker_health.json').write_text(json.dumps(health))
    assert not live.collect(tmp_path)['cohorts'][0]['evaluator_active']


@pytest.mark.parametrize('valid_cases', [160, 127])
def test_phygen_text_uses_three_stage_scores_and_requires_full_scope(tmp_path, monkeypatch, valid_cases):
    monkeypatch.setattr(live, 'COHORTS', {'phygenbench_text': ('PhyGenBench Text', 'legacy', 160)})
    monkeypatch.setattr(live, 'live_commands', lambda: [])
    generation = tmp_path / 'generations/phygenbench_text'
    generation.mkdir(parents=True)
    (generation / 'status.json').write_text(json.dumps({'groups': {
        'm/text': {'counts': {'complete': valid_cases, 'exhausted': 160-valid_cases}}}}))
    folder = tmp_path / 'evaluation/phygenbench_text_gpt55_three_stage_mcq_v1'
    folder.mkdir(parents=True)
    (folder / 'summary.json').write_text(json.dumps({
        'version': live.THREE_STAGE_REVISION, 'full160': True,
        'models': {'m': {'cases': valid_cases, 'mean': 0.6}}}))
    row = live.collect(tmp_path)['cohorts'][0]
    assert row['full_scope_scored'] is (valid_cases == 160)
    assert row['evaluation_progress'] == {'completed':valid_cases, 'expected':160}
    if valid_cases == 160:
        assert row['score']['metrics']['Overall'] == 0.6
        assert row['score']['judge_protocol'] == live.THREE_STAGE_REVISION
    else:
        assert row['score'] is None
        assert row['phase'] == 'incomplete_generation'


@pytest.mark.parametrize('version,scored', [('legacy',127), (live.THREE_STAGE_REVISION,160)])
def test_partial_phygen_progress_rejects_wrong_protocol_or_excess_scope(tmp_path, monkeypatch, version, scored):
    monkeypatch.setattr(live, 'COHORTS', {'phygenbench_text': ('PhyGenBench Text', 'legacy', 160)})
    monkeypatch.setattr(live, 'live_commands', lambda: [])
    generation = tmp_path / 'generations/phygenbench_text'
    generation.mkdir(parents=True)
    (generation / 'status.json').write_text(json.dumps({'groups': {
        'm/text': {'counts': {'complete':127, 'pending':33}}}}))
    folder = tmp_path / 'evaluation/phygenbench_text_gpt55_three_stage_mcq_v1'
    folder.mkdir(parents=True)
    (folder / 'summary.json').write_text(json.dumps({
        'version':version, 'full160':True, 'models':{'m':{'cases':scored, 'mean':0.6}}}))
    row = live.collect(tmp_path)['cohorts'][0]
    assert not row['full_scope_scored']
    assert row['score'] is None
    assert 'evaluation_progress' not in row
