import json
from pathlib import Path
import pytest

from pixelworld.native_harness import api_usage, Projection
from pixelworld.native_sandbox import sandbox_command


def test_native_cache_counts_are_included_once_in_input_total():
    result=api_usage({'input':100,'cacheRead':200,'cacheWrite':50,'output':20,'reasoning':5})
    assert result['input_tokens']==350
    assert result['cached_input_tokens']==200
    assert result['output_tokens']==20
    assert result['total_tokens']==370
    with pytest.raises(ValueError):api_usage({'input':100,'output':20})


def test_native_projection_deduplicates_response_usage(tmp_path,capsys):
    p=Projection(tmp_path/'rollout.jsonl')
    event={'type':'message_end','message':{'role':'assistant','responseId':'r1',
        'content':[],'stopReason':'stop','usage':{'input':100,'cacheRead':20,'cacheWrite':0,'output':8}}}
    p.consume(event);p.consume(event)
    assert p.totals['input_tokens']==120 and p.totals['output_tokens']==8
    p.session.close()
    assert len((tmp_path/'rollout.jsonl').read_text().splitlines())==1


def test_native_kernel_scope_excludes_credentials_and_other_case_roots(tmp_path):
    runtime=tmp_path/'runtime';(runtime/'bin').mkdir(parents=True);(runtime/'bin/bwrap').write_text('fixture')
    (runtime/'lib').mkdir();workspace=tmp_path/'case/workspace';workspace.mkdir(parents=True)
    command=sandbox_command(workspace,runtime,['/bin/true'])
    assert '--unshare-net' in command and '--clearenv' in command
    assert '/proc' not in command
    assert str(runtime/'secrets') not in command
    assert str(runtime) not in command
    assert str(workspace.parent) not in command
