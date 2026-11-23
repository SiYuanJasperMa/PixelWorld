import json
from types import SimpleNamespace

import pytest

from pixelworld.util import sha256
from tools.admit_controlled_harness_matrices import verified_canary


def test_canary_admission_requires_complete_video_and_exact_verified_archive(tmp_path):
    runs = tmp_path/'runs'
    archives = tmp_path/'archives'
    model = 'fixture-model'
    case = '0002'
    config = SimpleNamespace(runs_root=runs, output_token_budget=1_000_000,
        model=lambda _: SimpleNamespace(model='test-model',reasoning_effort='high',cli_backend='opencode'))
    run = runs/model/'vision-agent/case_0002_attempt_01'
    run.mkdir(parents=True)
    manifest = run/'run_manifest.json'
    row = {'status':'complete','model_key':model,'model':'test-model','reasoning_effort':'high',
           'track':'vision-agent','case_id':case,'attempt':1,'token_budget':{'budget_tokens':1_000_000},
           'token_usage':{'status':'recorded'},
           'validation':{'pass':True,'frame0_exact':True,'png_tensor_exact':True,'frames':120}}
    manifest.write_text(json.dumps(row))
    assert verified_canary(config,model,case,archives) is None
    benchmark = 'physics_iq_720p24fps-opencode'
    (run/'verified_archive_status.json').write_text(json.dumps({'status':'verified','benchmark':benchmark,
        'known_requests':1,'wire_pair_count':1,'run_number':1}))
    receipt = archives/benchmark/model/'run_0001/verified.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'source_run':str(run),'members':{'run_manifest.json':sha256(manifest)},
        'known_request_roles':{'resp_fixture':['input.json','response.json']},
        'verification':'independent SHA256 member readback'}))
    assert verified_canary(config,model,case,archives)[0] == run
    row['validation']['frame0_exact'] = False
    manifest.write_text(json.dumps(row))
    assert verified_canary(config,model,case,archives) is None
    row['validation']['frame0_exact'] = True
    row['new_revision'] = True
    manifest.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='exact delivered canary'):
        verified_canary(config,model,case,archives)
