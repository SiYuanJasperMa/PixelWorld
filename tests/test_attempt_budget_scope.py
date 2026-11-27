import json
import pytest
from pixelworld.config import load_config
from pixelworld.runner import _attempt_budget_usage


def make_config(root, scope=None):
    data={'inputs_root':'inputs','runs_root':'runs','output_token_budget':100,
          'models':[{'key':'fixture','model':'fixture','tracks':['text']}]}
    if scope is not None:data['output_token_budget_scope']=scope
    path=root/'config.json';path.write_text(json.dumps(data))
    return load_config(path)


@pytest.mark.parametrize('usage',[{'output_tokens':100},{'status':'missing','output_tokens':None}])
def test_new_attempt_has_full_budget_without_rewriting_previous_usage(tmp_path,usage):
    config=make_config(tmp_path);run=config.runs_root/'fixture/text/case_0001_attempt_01'
    run.mkdir(parents=True);path=run/'run_manifest.json'
    path.write_text(json.dumps({'status':'failed','token_usage':usage}));before=path.read_bytes()
    assert _attempt_budget_usage(config,config.models[0],'text','0001',2)==(0,False,[])
    assert path.read_bytes()==before


def test_explicit_legacy_window_keeps_cumulative_budget(tmp_path):
    config=make_config(tmp_path,'retry_window');run=config.runs_root/'fixture/text/case_0001_attempt_01'
    run.mkdir(parents=True);(run/'run_manifest.json').write_text(json.dumps({'status':'failed','token_usage':{'output_tokens':100}}))
    assert _attempt_budget_usage(config,config.models[0],'text','0001',2)[:2]==(100,False)


def test_unknown_budget_scope_is_rejected(tmp_path):
    with pytest.raises(ValueError,match='budget_scope'):make_config(tmp_path,'unknown')
