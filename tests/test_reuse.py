import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from pixelworld.reuse import run_sources
from pixelworld.supervisor import inspect
from tests.test_failed_retries import fixture, manifest
from tools.audit_reuse import audit, sha
from pixelworld.media import contract, task_rules


def historical(tmp_path):
    config, model = fixture(tmp_path)
    config = replace(config, filesystem_policy='case-only-v1', output_token_budget=100000)
    legacy = tmp_path / 'previous'
    run = legacy / model.key / 'text' / 'case_good_attempt_02'
    run.mkdir(parents=True)
    inputs = config.inputs_root / 'good'
    spec = contract(inputs)
    data = {'case_id':'good','model_key':'m','model':'fixture','track':'text','attempt':2,
            'status':'complete','validation':{'pass':True},'media_contract':spec,
            'generation_contract':'minimal-v2','protocol':config.protocol,
            'input_image_sha256':sha(inputs/'first_frame.png'),
            'input_prompt_sha256':sha(inputs/'prompt.txt'),
            'rules_sha256':hashlib.sha256(task_rules(spec).encode()).hexdigest(),
            'codex_version':'codex-cli 0.169.0','reasoning_effort':'high',
            'output_token_budget':100000,'duration_seconds':10}
    (run/'run_manifest.json').write_text(json.dumps(data))
    for name in ['generate.py','pixels.npz','inspection.png','transition_check.png','index.html','frames/00.png','frames/05.png']:
        p=run/'workspace'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'existing artifact')
    raw=json.loads(config.source.read_text());raw.update(filesystem_policy='case-only-v1',output_token_budget=100000)
    config.source.write_text(json.dumps(raw))
    return config, model, legacy, run, data


def test_reuse_preserves_history_and_stops_duplicate_dispatch(tmp_path):
    config,model,legacy,run,data=historical(tmp_path)
    before=(run/'run_manifest.json').read_bytes()
    assert not audit(config.source,legacy)['eligible']
    result=audit(config.source,legacy,compatible_history=True)
    assert len(result['eligible'])==1
    config.runs_root.mkdir()
    (config.runs_root/'reuse_registry.json').write_text(json.dumps({'schema':'pixelworld-compatible-history-v1','entries':result['eligible']}))
    new=manifest(config,model,'good',2,{'status':'failed'})
    state=inspect(config,model,'text','good')
    assert state['state']=='complete' and state['reused']
    assert state['run']==str(run)
    assert (run/'run_manifest.json').read_bytes()==before
    assert json.loads((new/'run_manifest.json').read_text())['status']=='failed'
    refs=run_sources(new.parent,'good')
    assert len(refs)==2 and len({r[1]for r in refs})==2
    assert any(r[2] is not None and r[1].startswith('history_')for r in refs)


def test_reuse_rejects_changed_input_or_budget_above_allowance(tmp_path):
    config,model,legacy,run,data=historical(tmp_path)
    (config.inputs_root/'good/prompt.txt').write_text('Different prompt')
    assert audit(config.source,legacy,True)['exclusion_checks']['prompt']==1
    data['output_token_budget']=2000000
    (run/'run_manifest.json').write_text(json.dumps(data))
    assert audit(config.source,legacy,True)['exclusion_checks']['budget']==1


def test_reuse_fails_closed_on_altered_source_manifest(tmp_path):
    config,model,legacy,run,data=historical(tmp_path)
    result=audit(config.source,legacy,True)
    config.runs_root.mkdir()
    (config.runs_root/'reuse_registry.json').write_text(json.dumps({'schema':'pixelworld-compatible-history-v1','entries':result['eligible']}))
    assert inspect(config,model,'text','good')['state']=='complete'
    data['model']='someone else';(run/'run_manifest.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='changed after reuse audit'):
        inspect(config,model,'text','good')
