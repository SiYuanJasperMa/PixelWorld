import json
from types import SimpleNamespace

import pytest

from pixelworld.util import sha256
from tools.watch_rbench_common_scores import GPT61_JUDGE, PROTOCOL, UPSTREAM, numeric_score, read_pilot


def test_pilot_only_reused_for_same_source_and_prompt_revision(tmp_path):
    pilot = tmp_path/'pilot'
    (pilot/'evaluation').mkdir(parents=True)
    (pilot/'videos').mkdir()
    prompts = tmp_path/'prompts.json'
    prompts.write_text('[]')
    runs = tmp_path/'runs'
    run = runs/'gpt61-sol/vision-agent/case_0001_attempt_01'
    run.mkdir(parents=True)
    (pilot/'rbench_case_0001.json').write_text(json.dumps({'runs_root':str(runs)}))
    (pilot/'evaluation/summary.json').write_text(json.dumps({
        'protocol':PROTOCOL,'official_code_commit':UPSTREAM,'judge':GPT61_JUDGE,
        'manifest_sha256':sha256(prompts),'failed_cases':[],'scored_cases':1,
    }))
    (pilot/'evaluation/results.csv').write_text('name,details\ncommon_manipulation_0001,"{\'score\': 3}"\n')
    (pilot/'videos/0001.mp4').write_bytes(b'original pilot')
    official=SimpleNamespace(compute_final_score=lambda r:r['score'])
    assert read_pilot(official,pilot,prompts,run,'gpt61-sol','0001')['score']==3
    prompts.write_text('["changed"]')
    assert read_pilot(official,pilot,prompts,run,'gpt61-sol','0001') is None


def test_official_score_rejects_nonfinite_value():
    scorer=SimpleNamespace(compute_final_score=lambda r:r['score'])
    with pytest.raises(ValueError,match='finite'):
        numeric_score(scorer,{'score':float('inf')})


def test_rbench_accepts_only_infra_predecessors(tmp_path):
    from tools.watch_rbench_common_scores import manifest_info
    root=tmp_path/'m'/'vision-agent'
    prior=root/'case_0001_attempt_01';prior.mkdir(parents=True)
    run=root/'case_0001_attempt_02';run.mkdir()
    original={'status':'failed','case_id':'0001','model_key':'m','attempt':1,
              'validation':{'infra_error':'missing_runtime'}}
    (prior/'run_manifest.json').write_text(json.dumps(original))
    final={'status':'complete','case_id':'0001','model_key':'m','attempt':2,
           'validation':{'pass':True},'media_contract':{'frames':120,'fps':24,'task_type':'i2v'}}
    (run/'run_manifest.json').write_text(json.dumps(final))
    assert manifest_info(run,'0001','m')['attempt']==2
    original['validation']={}
    (prior/'run_manifest.json').write_text(json.dumps(original))
    with pytest.raises(ValueError,match='reroll'):
        manifest_info(run,'0001','m')
    (prior/'run_manifest.json').unlink()
    with pytest.raises(ValueError,match='Missing predecessor'):
        manifest_info(run,'0001','m')
