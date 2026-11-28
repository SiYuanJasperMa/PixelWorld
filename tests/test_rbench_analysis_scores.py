from tools.build_analysis_dashboard import rbench_evaluation,phygen_evaluation
import subprocess
import sys


def fixture(**row):
    return {'protocol': 'rbench-common-official-gpt-branch-gpt61-sol-v1',
            'judge': 'traex/GPT-6.1-Sol', 'models': {'text_model': {'expected':50,**row}}}


def test_complete_text_score_uses_official_normalization():
    result = rbench_evaluation(fixture(scored=50, full_common50=True, mean=3), 'text_model')
    assert result['full_scope_scored']
    assert result['score']['metrics']['Normalized score'] == .5


def test_partial_or_missing_text_decisions_never_claim_full_score():
    result = rbench_evaluation(fixture(scored=49, full_common50=True, mean=3), 'text_model')
    assert not result['full_scope_scored']
    assert result['score'] is None
    assert result['evaluation_progress']['completed'] == 49


def test_foreign_judge_and_invalid_counter_are_not_adopted():
    foreign = fixture(scored=50, full_common50=True, mean=3)
    foreign['judge'] = 'gpt-5.5'
    assert rbench_evaluation(foreign, 'text_model') is None
    assert rbench_evaluation(fixture(scored=51, full_common50=True, mean=3), 'text_model') is None


def test_rbench_pending_judgments_are_not_a_complete_analysis():
    result=rbench_evaluation(fixture(scored=50,full_common50=True,mean=3,judge_errors=1),'text_model')
    assert not result['full_scope_scored'] and result['score'] is None
    assert not rbench_evaluation(fixture(scored=50,expected=49,full_common50=True,mean=3),'text_model')['full_scope_scored']


def test_current_phygen_text_adapter_requires_full_current_protocol():
    report={'version':'phygen-gpt61-sol-three-stage-mcq-v1','judge':'traex/GPT-6.1-Sol',
            'cases':list(range(1,161)),'full160':True,'models':{'text_model':{'cases':160,'mean':.6}}}
    result=phygen_evaluation(report,'text_model')
    assert result['full_scope_scored'] and result['score']['metrics']['Overall']==.6
    partial={**report,'models':{'text_model':{'cases':159,'mean':.6}}}
    assert not phygen_evaluation(partial,'text_model')['full_scope_scored']
    assert phygen_evaluation({**report,'judge':'gpt-5.5'},'text_model') is None


def test_progress_reader_does_not_load_the_judge_sdk():
    code="""import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name=='openai' or name.startswith('openai.'):
        raise ImportError('The progress reader has no judge SDK')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from tools.build_analysis_dashboard import phygen_evaluation
report={'version':'phygen-gpt61-sol-three-stage-mcq-v1','judge':'traex/GPT-6.1-Sol','full160':True,'cases':list(range(1,161)),'models':{'model':{'cases':160,'mean':.6}}}
assert phygen_evaluation(report,'model')['full_scope_scored']
"""
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)
