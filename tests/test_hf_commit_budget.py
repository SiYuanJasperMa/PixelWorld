import json
from types import SimpleNamespace
import pytest

from tools.hf_commit_budget import commit, defer, reserve, retry_delay


def test_publishers_share_slots_and_cooldown(tmp_path):
    state = tmp_path / 'budget.json'
    assert reserve(state, 100) == 100
    assert reserve(state, 101) == 145
    defer(state, 1000, 'rate limit')
    assert reserve(state, 102) == 1000
    assert reserve(state, 103) == 1045
    assert json.loads(state.read_text())['cooldown_until_unix'] == 1000


def test_retry_delay_uses_response_header_then_server_message():
    class Error(Exception):
        def __init__(self, message, status, headers):
            super().__init__(message)
            self.response = SimpleNamespace(status_code=status, headers=headers)
    assert retry_delay(Error('retry in about 1 hour', 429, {'Retry-After': '17'})) == 17
    assert retry_delay(Error('retry this action in about 1 hour', 429, {})) == 3600
    assert retry_delay(Error('unavailable', 503, {})) is None


def test_commit_failure_propagates_and_records_shared_retry_window(tmp_path, monkeypatch):
    state = tmp_path / 'budget.json'
    monkeypatch.setattr('tools.hf_commit_budget.time.time', lambda: 100)
    error = RuntimeError('rate limited')
    error.response = SimpleNamespace(status_code=429, headers={'Retry-After': '17'})
    calls = []
    def failed(**kwargs):
        calls.append(kwargs)
        raise error
    with pytest.raises(RuntimeError, match='rate limited'):
        commit(SimpleNamespace(create_commit=failed), state=state, repo_id='dataset', operations=['original'])
    assert calls == [{'repo_id': 'dataset', 'operations': ['original']}]
    assert json.loads(state.read_text())['cooldown_until_unix'] == 117


def test_latest_operations_are_read_after_the_commit_slot_opens(tmp_path, monkeypatch):
    clock = [100]
    latest = ['old']
    state = tmp_path / 'budget.json'
    defer(state, 110, 'retry window')
    monkeypatch.setattr('tools.hf_commit_budget.time.time', lambda: clock[0])
    def sleep(seconds):
        clock[0] += seconds
        latest[0] = 'current'
    monkeypatch.setattr('tools.hf_commit_budget.time.sleep', sleep)
    calls = []
    api = SimpleNamespace(create_commit=lambda **kwargs: calls.append(kwargs))
    commit(api, state=state, operations_factory=lambda: list(latest), repo_id='dataset')
    assert calls == [{'repo_id': 'dataset', 'operations': ['current']}]


def test_default_commit_budget_can_use_writable_control_storage(tmp_path,monkeypatch):
    state=tmp_path/'control/shared_budget.json'
    monkeypatch.setenv('PIXELWORLD_HF_COMMIT_BUDGET_STATE',str(state))
    monkeypatch.setattr('tools.hf_commit_budget.time.time',lambda:100)
    calls=[]
    commit(SimpleNamespace(create_commit=lambda **kwargs:calls.append(kwargs)),repo_id='dataset',operations=['same'])
    assert calls==[{'repo_id':'dataset','operations':['same']}]
    assert json.loads(state.read_text())['last_reserved_unix']==100


def test_commit_budget_quota_wait_keeps_one_slot_and_one_submission(tmp_path,monkeypatch):
    import errno
    from pathlib import Path
    state=tmp_path/'budget.json';opened=Path.open;rejected=[]
    def interrupted(path,*args,**kwargs):
        if path.name.startswith('budget.json.tmp.')and not rejected:
            rejected.append(path);raise OSError(errno.EDQUOT,'quota fixture')
        return opened(path,*args,**kwargs)
    monkeypatch.setenv('PIXELWORLD_PERSIST_STORAGE_WAIT','1')
    monkeypatch.setattr(Path,'open',interrupted)
    monkeypatch.setattr('pixelworld.util.time.sleep',lambda delay:None)
    monkeypatch.setattr('tools.hf_commit_budget.time.time',lambda:100)
    calls=[]
    commit(SimpleNamespace(create_commit=lambda **kwargs:calls.append(kwargs)),state=state,repo_id='dataset',operations=['same'])
    assert len(rejected)==1 and len(calls)==1
    assert json.loads(state.read_text())['last_reserved_unix']==100
