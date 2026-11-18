from dataclasses import replace
import json

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
