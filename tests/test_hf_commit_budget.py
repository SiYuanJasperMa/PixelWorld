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
