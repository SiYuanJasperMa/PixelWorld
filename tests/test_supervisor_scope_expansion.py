import json
from pathlib import Path
import pytest

from pixelworld.config import load_config
from pixelworld.supervisor import expand_case_scope


def setup(tmp_path):
    inputs=tmp_path/'inputs';inputs.mkdir()
    for case in ['one','two']:
        path=inputs/case;path.mkdir();(path/'prompt.txt').write_text('prompt')
    path=tmp_path/'matrix.json'
    raw={'inputs_root':str(inputs),'runs_root':str(tmp_path/'runs'),'case_ids':['one'],
         'models':[{'key':'model','model':'model','tracks':['vision-agent']}],
         'output_token_budget':1000000,'max_attempts':1,'max_total_attempts':5}
    path.write_text(json.dumps(raw))
    return path,raw,load_config(path)


def test_prepared_case_is_added_without_restart(tmp_path):
    path,raw,config=setup(tmp_path);raw['case_ids'].append('two');path.write_text(json.dumps(raw))
    result=expand_case_scope(config)
    assert result.case_ids==('one','two')
    assert result.protocol==config.protocol and result.models==config.models


def test_protocol_or_budget_change_is_not_adopted(tmp_path):
    path,raw,config=setup(tmp_path);raw['case_ids'].append('two');raw['output_token_budget']=10
    path.write_text(json.dumps(raw));assert expand_case_scope(config)==config


def test_case_removal_or_unprepared_addition_is_not_adopted(tmp_path):
    path,raw,config=setup(tmp_path);raw['case_ids']=['two'];path.write_text(json.dumps(raw))
    assert expand_case_scope(config)==config
    raw['case_ids']=['one','missing'];path.write_text(json.dumps(raw))
    assert expand_case_scope(config)==config


def test_duplicate_expansion_is_rejected(tmp_path):
    path,raw,config=setup(tmp_path);raw['case_ids']=['one','two','two'];path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='Duplicate case IDs'):expand_case_scope(config)
