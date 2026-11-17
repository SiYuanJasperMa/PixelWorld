from types import SimpleNamespace
from tools.upload_hf_results import ApiGovernor, LimitedSession


def test_hf_quota_sets_shared_cooldown(monkeypatch):
    import tools.upload_hf_results as module
    monkeypatch.setattr(module.time, 'monotonic', lambda: 100)
    limiter = ApiGovernor()
    limiter.reject('25')
    assert limiter.cooldown_until == 125
    limiter.reject('2')
    assert limiter.cooldown_until == 125


def test_only_hf_api_requests_are_paced(monkeypatch):
    import requests
    limiter = SimpleNamespace(wait=lambda: calls.append('wait'),
                              reject=lambda retry: calls.append(('quota', retry)))
    calls = []
    monkeypatch.setattr(requests.Session, 'send',
        lambda *_args, **_kwargs: SimpleNamespace(status_code=429, headers={'Retry-After': '10'}))
    session = LimitedSession(limiter)
    session.send(SimpleNamespace(url='https://huggingface.co/api/test'))
    assert calls == ['wait', ('quota', '10')]
    calls.clear()
    session.send(SimpleNamespace(url='https://cdn-lfs.hf.co/video'))
    assert calls == []
