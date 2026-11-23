import json

import pytest

from tools import drive_registered_matrices as queue


def make_queue(tmp_path):
    config = tmp_path/'cohort.json'
    runs = tmp_path/'runs'
    config.write_text(json.dumps({'runs_root':str(runs),'inputs_root':str(tmp_path/'inputs'),
        'case_ids':['0001','0002'],'models':[{'key':'fixture','model':'fixture'}]}))
    row = {'name':'cohort','config':str(config),'runs_root':str(runs),'expected':2,'state':'queued'}
    spec = tmp_path/'worker_1.json'
    status = spec.with_suffix('.status.json')
    registry = tmp_path/'registered.json'
    spec.write_text(json.dumps([row]))
    status.write_text(json.dumps([row]))
    registry.write_text(json.dumps({'configured':[row]}))
    return spec, status, registry, row


def test_queue_preserves_the_registered_scope(tmp_path):
    spec,status,registry,row = make_queue(tmp_path)
    queues, commands = queue.read_queue([spec],registry,tmp_path/'bin')
    assert queues[status] == [row]
    assert commands['cohort'][-4:] == ['--config',row['config'],'--interval','20']
    status.write_text(json.dumps([{**row,'expected':1}]))
    with pytest.raises(ValueError,match='registered cases'):
        queue.read_queue([spec],registry,tmp_path/'bin')


def test_adoption_checks_pid_start_marker(tmp_path, monkeypatch):
    spec,status,registry,row = make_queue(tmp_path)
    _,commands = queue.read_queue([spec],registry,tmp_path/'bin')
    status.write_text(json.dumps([{**row,'state':'supervisor_started','pid':123,'pid_start_marker':'99'}]))
    monkeypatch.setattr(queue,'process_identity',lambda _: ('99',commands['cohort']))
    states,_ = queue.read_queue([spec],registry,tmp_path/'bin')
    assert states[status][0]['adopted'] is True
    monkeypatch.setattr(queue,'process_identity',lambda _: ('100',commands['cohort']))
    with pytest.raises(ValueError,match='identity'):
        queue.read_queue([spec],registry,tmp_path/'bin')
