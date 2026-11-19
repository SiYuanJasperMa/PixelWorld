import json
from types import SimpleNamespace
from tools.remote_case_status import remote_case_status


def setup_status(tmp_path):
    config = SimpleNamespace(runs_root=tmp_path)
    model = SimpleNamespace(key='model')
    path = tmp_path/'status.json'
    path.write_text(json.dumps({'groups':{'model/vision-agent':{'running_cases':['case1'],'exhausted_cases':[]}}}))
    return config, model, path


def test_remote_running_pid_is_not_a_model_failure(tmp_path):
    config,model,path=setup_status(tmp_path)
    result=remote_case_status(config,model,'vision-agent','case1',{'state':'exhausted'},now=path.stat().st_mtime+1)
    assert result['state']=='running'


def test_stale_remote_running_manifest_is_infra_uncertainty(tmp_path):
    config,model,path=setup_status(tmp_path)
    run=tmp_path/'model/vision-agent/case_case1_attempt_01';run.mkdir(parents=True)
    (run/'run_manifest.json').write_text(json.dumps({'status':'running'}))
    result=remote_case_status(config,model,'vision-agent','case1',{'state':'exhausted'},now=path.stat().st_mtime+121)
    assert result['state']=='infra_stale'


def test_completed_delivery_remains_authoritative(tmp_path):
    config,model,path=setup_status(tmp_path)
    result=remote_case_status(config,model,'vision-agent','case1',{'state':'complete'},now=path.stat().st_mtime+121)
    assert result['state']=='complete'


def test_closed_window_before_five_attempts_is_waiting_retry(tmp_path):
    config,model,path=setup_status(tmp_path);config.max_total_attempts=5
    path.write_text(json.dumps({'groups':{'model/vision-agent':{'running_cases':[],'exhausted_cases':['case1']}}}))
    result=remote_case_status(config,model,'vision-agent','case1',{'state':'exhausted','model_attempts':1},now=path.stat().st_mtime+1)
    assert result['state']=='retry_pending'
