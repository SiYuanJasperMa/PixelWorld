import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from pixelworld.constants import ASSET_ROOT
from pixelworld.submission import handle, submit
from pixelworld.audit import validate


def fixture(tmp_path):
    run=tmp_path/'run';workspace=run/'workspace';workspace.mkdir(parents=True)
    logs=run/'logs';logs.mkdir();source=run/'input_snapshot';source.mkdir()
    spec={'task_type':'i2v','width':24,'height':16,'frames':6,'fps':3,'audio':None}
    (run/'media_contract.json').write_text(json.dumps(spec))
    (workspace/'task_spec.json').write_text(json.dumps(spec))
    (workspace/'prompt.txt').write_text('Fixture RGB video.')
    shutil.copy2(ASSET_ROOT/'player_template.html',workspace/'player_template.html')
    Image.new('RGB',(24,16),'red').save(workspace/'first_frame.png')
    Image.new('RGB',(24,16),'red').save(source/'first_frame.png')
    (run/'run_manifest.json').write_text(json.dumps({'harness_variant':'flash-submit',
        'filesystem_policy':'case-only-v1','track':'vision-agent'}))
    (workspace/'generate.py').write_text('import numpy as np\n')
    (logs/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{
        'type':'command_execution','command':'python generate.py','status':'completed','exit_code':0}})+'\n')
    module_spec=importlib.util.spec_from_file_location('output_writer',ASSET_ROOT/'artifact_io.py')
    writer=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(writer)
    frames=np.zeros((6,16,24,3),dtype=np.uint8);frames[:,:,:,0]=255
    writer.save(frames,workspace)
    return run,spec


def test_submission_checks_original_contract_without_inventing_turn_completion(tmp_path):
    run,spec=fixture(tmp_path)
    normal=validate(run/'workspace',run/'logs/events.jsonl',-1,'vision-agent',require_reports=False,media=spec)
    assert not normal['pass']
    result=submit(run)
    assert result['accepted'] and not result['physics_score_provided']
    receipt=json.loads((run/'logs/submission_receipt.json').read_text())
    assert receipt['validation']['completion_mode']=='submission_requested'
    assert receipt['validation']['terminal_events']==[]
    assert not receipt['pixels_generated']
    assert 'turn.completed' not in (run/'logs/events.jsonl').read_text()


def test_privileged_submission_rejects_symlink_escape(tmp_path):
    run,_=fixture(tmp_path);tensor=run/'workspace/pixels.npz'
    outside=tmp_path/'private.npz';tensor.replace(outside);tensor.symlink_to(outside)
    with pytest.raises(ValueError,match='leaves the case'):
        submit(run)
    assert not (run/'logs/submission_receipt.json').exists()


def test_original_first_frame_and_execution_policy_are_enforced(tmp_path):
    run,_=fixture(tmp_path)
    Image.new('RGB',(24,16),'blue').save(run/'input_snapshot/first_frame.png')
    result=submit(run)
    assert not result['accepted'] and any('frame zero' in x for x in result['errors'])
    Image.new('RGB',(24,16),'red').save(run/'input_snapshot/first_frame.png')
    with (run/'logs/events.jsonl').open('a')as f:
        f.write(json.dumps({'type':'item.completed','item':{'type':'command_execution',
            'command':'cat ../other_case/score.json','status':'failed','exit_code':1}})+'\n')
    assert not submit(run)['accepted']


def test_mcp_tool_has_no_arbitrary_path_or_score_interface(tmp_path):
    tools=handle({'method':'tools/list'},tmp_path)['tools']
    assert tools[0]['inputSchema']['properties']=={}
    with pytest.raises(ValueError,match='no file paths'):
        handle({'method':'tools/call','params':{'name':'submit_video','arguments':{'path':'../private'}}},tmp_path)


def test_frame_symlink_and_oversized_archive_are_rejected_before_loading(tmp_path):
    run,_=fixture(tmp_path);frame=run/'workspace/frames/00.png'
    outside=tmp_path/'outside.png';frame.replace(outside);frame.symlink_to(outside)
    with pytest.raises(ValueError,match='Frame path leaves'):
        submit(run)
    frame.unlink();outside.replace(frame)
    with zipfile.ZipFile(run/'workspace/pixels.npz','w',compression=zipfile.ZIP_DEFLATED)as archive:
        archive.writestr('pixels.npy',b'0'*(2*1024**2))
    with pytest.raises(ValueError,match='oversized'):
        submit(run)


def test_submission_runner_trust_uses_only_an_isolated_empty_config(tmp_path):
    from pixelworld.config import load_config
    from pixelworld.runner import run_case
    root=tmp_path/'inputs/one';root.mkdir(parents=True)
    (root/'prompt.txt').write_text('Generate a fixture RGB video.')
    Image.new('RGB',(24,16),'red').save(root/'first_frame.png')
    (root/'task_spec.json').write_text(json.dumps({'task_type':'i2v','width':24,'height':16,'frames':6,'fps':3,'audio':None}))
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'harness_variant':'flash-submit','filesystem_policy':'case-only-v1',
        'inputs_root':'inputs','runs_root':'runs','case_ids':['one'],
        'models':[{'key':'m','model':'fixture','tracks':['vision-agent']}]}))
    record=run_case(load_config(path),'m','vision-agent','one',codex_binary='/bin/true',dry_run=True)
    assert '--trust' in record['command'] and '--ignore-user-config' not in record['command']
    assert '--ignore-rules' in record['command'] and record['filesystem_policy']=='case-only-v1'
    assert (Path(record['isolated_codex_home'])/'config.toml').read_text()==''


def test_cli_configuration_error_requires_no_model_events(tmp_path):
    from pixelworld.runner import _execution_infra_failure
    (tmp_path/'stderr.log').write_text('Error: --trust cannot be combined with --ignore-user-config')
    (tmp_path/'events.jsonl').write_text('')
    assert _execution_infra_failure(tmp_path)=='local_cli_configuration_failure'
    (tmp_path/'events.jsonl').write_text('{"type":"turn.started"}\n')
    assert _execution_infra_failure(tmp_path) is None
