import argparse
import json

import pytest

from tools.final_experiments import start
from pixelworld.runner import generation_task_text


def test_full_launch_cannot_bypass_review_or_missing_gates(tmp_path):
    (tmp_path/'bundle.json').write_text(json.dumps({'configs':{}}))
    (tmp_path/'READINESS.json').write_text(json.dumps({'approved':False,'gates':{'runtime':False}}))
    args=argparse.Namespace(bundle=tmp_path,full=True,review_approved=False)
    with pytest.raises(ValueError,match='review-approved'):start(args)
    args.review_approved=True
    with pytest.raises(ValueError,match='runtime'):start(args)


def test_review_prompt_uses_the_same_task_builder_as_execution():
    t2v={'task_type':'t2v'}
    text=generation_task_text('vision-agent',t2v)
    assert 'There is no input image' in text
    assert 'Visual image tools are available' in text
    assert 'considering efficiency' in text
    edit=generation_task_text('text',{'task_type':'video_edit'})
    assert 'complete source video' in edit
    assert 'artifact_io.save' in edit
    assert 'visual image tools are disabled' in edit


def test_generation_launch_still_requires_runtime_and_budget_acceptance(tmp_path):
    (tmp_path/'bundle.json').write_text(json.dumps({'configs':{}}))
    (tmp_path/'READINESS.json').write_text(json.dumps({'approved':True,'gates':{
        'generation_runtime':True,'filesystem_isolation':True,'attempt_and_usage_tests':False,
        'official_evaluator_smokes':False}}))
    args=argparse.Namespace(bundle=tmp_path,full=True,review_approved=True,generation_only=True)
    with pytest.raises(ValueError,match='attempt_and_usage_tests'):
        start(args)
