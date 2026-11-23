import json

from tools.watch_benchmark_services import complete_service
from tools.observe_minimax_gpus import effective_utilization


def test_partial_full_scope_summary_does_not_stop_monitor(tmp_path):
    path=tmp_path/'summary.json'
    service={'completion_path':str(path),'completion_checks':{
        'full160':True,'completed_jobs':160,'expected_jobs':160,'failures':[]}}
    assert not complete_service(service)
    path.write_text(json.dumps({'full160':True,'completed_jobs':1,'expected_jobs':160,'failures':[]}))
    assert not complete_service(service)
    path.write_text(json.dumps({'full160':True,'completed_jobs':160,'expected_jobs':160,'failures':[]}))
    assert complete_service(service)


def test_unrelated_or_invalid_summary_cannot_mark_pcve_complete(tmp_path):
    path=tmp_path/'summary.json'
    service={'completion_path':str(path),'completion_checks':{
        'complete_official129':True,'case_count':129,'sam2_native_postprocessing':True}}
    path.write_text(json.dumps({'case_count':10,'complete_official129':False}))
    assert not complete_service(service)
    path.write_text('invalid')
    assert not complete_service(service)
    assert not complete_service({})


def test_keepalive_utilization_is_not_counted_as_generation():
    assert effective_utilization({'utilization_gpu':100,'keeper_only':True})==0
    assert effective_utilization({'utilization_gpu':95,'keeper_only':False})==95
