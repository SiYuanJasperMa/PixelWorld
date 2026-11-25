from dataclasses import replace
import json
import pytest

from pixelworld.retries import authorize_near_complete, model_attempt_cap, authorize_failed
from pixelworld.supervisor import inspect
from tests.test_failed_retries import fixture, manifest


def nearly_done(tmp_path, good=19):
    config,model=fixture(tmp_path)
    config=replace(config,max_attempts=5,max_total_attempts=5,
                   case_ids=tuple(['bad']+[f'good{i}'for i in range(good)]))
    for case in config.case_ids[1:]:manifest(config,model,case,1,{'status':'complete','validation':{'pass':True}})
    for attempt in range(1,6):manifest(config,model,'bad',attempt,{'status':'failed'})
    return config,model


def test_sparse_rescue_keeps_global_cap_and_original_failures(tmp_path):
    config,model=nearly_done(tmp_path)
    old=manifest(config,model,'bad',5,{'status':'failed'})/'run_manifest.json';before=old.read_bytes()
    result=authorize_near_complete(config,apply=True)
    assert result['authorized_cases']==1
    assert result['items'][0]['model_attempt_cap']==6
    assert config.max_total_attempts==5 and old.read_bytes()==before
    assert inspect(config,model,'text','bad')['state']=='pending'
    assert model_attempt_cap(config,model,'text','bad')==6
    assert model_attempt_cap(config,model,'text','good0')==5
    assert authorize_near_complete(config,apply=True)['authorized_cases']==0


def test_sparse_rescue_is_bounded_and_counts_budget_failure(tmp_path):
    config,model=nearly_done(tmp_path)
    authorize_near_complete(config,apply=True)
    manifest(config,model,'bad',6,{'status':'failed','stop_reason':'output_token_budget_exhausted'})
    result=authorize_near_complete(config,apply=True)
    assert result['items'][0]['model_attempt_cap']==7
    manifest(config,model,'bad',7,{'status':'failed'})
    assert authorize_near_complete(config,apply=True)['authorized_cases']==0
    assert inspect(config,model,'text','bad')['state']=='exhausted'


def test_low_delivery_cohort_is_not_given_rescues(tmp_path):
    config,model=nearly_done(tmp_path,good=3)
    assert authorize_near_complete(config,apply=True)['authorized_cases']==0
    assert model_attempt_cap(config,model,'text','bad')==5


def test_95_percent_large_cohort_is_not_blocked_by_absolute_three_case_limit(tmp_path):
    config,model=fixture(tmp_path)
    good=[f'good{i}'for i in range(153)];bad=[f'bad{i}'for i in range(7)]
    config=replace(config,max_attempts=5,max_total_attempts=5,case_ids=tuple(good+bad))
    for case in good:manifest(config,model,case,1,{'status':'complete','validation':{'pass':True}})
    for case in bad:
        for attempt in range(1,6):manifest(config,model,case,attempt,{'status':'failed'})
    assert authorize_near_complete(config,max_missing=3,apply=False)['authorized_cases']==0
    result=authorize_near_complete(config,apply=True)
    assert result['authorized_cases']==7
    assert all(row['model_attempt_cap']==6 for row in result['items'])
    assert config.max_total_attempts==5


def test_infrastructure_retries_keep_scoped_real_attempt_cap(tmp_path):
    config,model=nearly_done(tmp_path)
    authorize_near_complete(config,apply=True)
    manifest(config,model,'bad',6,{'status':'failed','validation':{'infra_error':'storage'}})
    result=authorize_failed(config,extra_attempts=1,max_rounds=None,apply=True)
    assert result['items'][0]['start_attempt']==7
    assert result['items'][0]['model_attempt_cap']==6
    success=manifest(config,model,'bad',7,{'status':'complete','validation':{'pass':True}})
    assert authorize_near_complete(config,apply=True)['authorized_cases']==0
    ledger=json.loads((config.runs_root/'retry_authorizations.json').read_text())
    assert any(row.get('completed_manifest_sha256')for row in ledger['cases']['m/text/bad'])
    assert inspect(config,model,'text','bad')['state']=='complete'


def test_explicit_owner_tail_retry_stops_at_first_valid_delivery(tmp_path):
    config,model=nearly_done(tmp_path)
    for attempt in range(6,10):
        result=authorize_near_complete(config,until_first_delivery=True,apply=True)
        assert result['items'][0]['model_attempt_cap']==attempt
        assert result['items'][0]['until_first_delivery'] is True
        assert config.max_total_attempts==5
        manifest(config,model,'bad',attempt,{'status':'failed'})
    authorize_near_complete(config,until_first_delivery=True,apply=True)
    manifest(config,model,'bad',10,{'status':'complete','validation':{'pass':True}})
    assert authorize_near_complete(config,until_first_delivery=True,apply=True)['authorized_cases']==0
    assert inspect(config,model,'text','bad')['state']=='complete'


def test_owner_tail_policy_survives_an_infrastructure_retry(tmp_path):
    config,model=nearly_done(tmp_path)
    for attempt in (6,7):
        authorize_near_complete(config,until_first_delivery=True,apply=True)
        manifest(config,model,'bad',attempt,{'status':'failed'})
    authorize_near_complete(config,until_first_delivery=True,apply=True)
    manifest(config,model,'bad',8,{'status':'failed','validation':{'infra_error':'storage'}})
    result=authorize_failed(config,extra_attempts=1,max_rounds=None,apply=True)
    assert result['items'][0]['start_attempt']==9
    assert result['items'][0]['model_attempt_cap']==8
    assert result['items'][0]['until_first_delivery'] is True
    assert model_attempt_cap(config,model,'text','bad')==8


def test_explicit_model_recovery_does_not_extend_another_model(tmp_path):
    config,model=nearly_done(tmp_path,good=3)
    assert authorize_near_complete(config, min_fraction=0, until_first_delivery=True,
        model_keys={'another-model'}, apply=True)['authorized_cases']==0
    result=authorize_near_complete(config, min_fraction=0, until_first_delivery=True,
        model_keys={model.key}, apply=True)
    assert result['authorized_cases']==1
    manifest(config,model,'bad',6,{'status':'complete','validation':{'pass':True}})
    assert authorize_near_complete(config, min_fraction=0, until_first_delivery=True,
        model_keys={model.key}, apply=True)['authorized_cases']==0


@pytest.mark.parametrize('other_state',['pending','running'])
def test_explicit_all_failed_recovery_does_not_wait_for_other_cases(tmp_path,monkeypatch,other_state):
    import pixelworld.supervisor as supervisor
    config,model=fixture(tmp_path)
    config=replace(config,max_attempts=5,max_total_attempts=5)
    for attempt in range(1,6):
        manifest(config,model,'bad',attempt,{'status':'failed'})
    original=supervisor.inspect
    def state(c,m,t,case):
        return {'state':other_state} if case=='good' else original(c,m,t,case)
    monkeypatch.setattr(supervisor,'inspect',state)
    assert authorize_near_complete(config,until_first_delivery=True,apply=True)['authorized_cases']==0
    result=authorize_near_complete(config,min_fraction=0,until_first_delivery=True,
                                  model_keys={model.key},apply=True)
    assert [row['key'] for row in result['items']]==['m/text/bad']
    assert result['items'][0]['start_attempt']==6
    assert model_attempt_cap(config,model,'text','good')==5
