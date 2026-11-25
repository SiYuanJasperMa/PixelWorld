"""Exercise the actual local bridge with a deterministic native SSE server."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
from threading import Lock, Thread
from urllib.request import Request, urlopen

import pytest


@contextmanager
def bridge(tmp_path, monkeypatch, reason, default=128000, use_request_parameters=False):
    source = Path(__file__).resolve().parents[1]/'pixelworld/traex_responses_proxy.py'
    if not source.exists():
        pytest.skip('Local TRAE bridge is not installed')
    spec = importlib.util.spec_from_file_location('completion_test_bridge', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MODEL_CONFIG={'GLM-5.4': {'config_name':'glm-test','native_model_name':'glm-test'}}
    module.MODELS={'GLM-5.4':'glm-test'}
    seen = []

    class Native:
        status_code = 200
        def iter_lines(self, **kwargs):
            for event, data in [('token_usage', {'prompt_tokens': 7, 'completion_tokens': 32000}),
                                ('done', {'finish_reason': reason})]:
                yield 'event: '+event
                yield 'data: '+json.dumps(data)
                yield ''
        def close(self): pass

    class Session:
        def post(self, url, **kwargs):
            seen.append(kwargs['json'])
            return Native()
        def close(self): pass

    monkeypatch.setattr(module.requests, 'Session', Session)
    auth = tmp_path/'auth.json'
    auth.write_text(json.dumps({'trae': {'access_token': 'fake-test-token',
                                       'credential_kind': 'cloud_cli_jwt'}}))
    server = ThreadingHTTPServer(('127.0.0.1', 0), module.Bridge)
    server.local_key = 'test-only-key'
    server.auth_file = auth
    server.upstream = 'https://unused.invalid'
    server.log_dir = tmp_path
    server.lock = Lock()
    server.max_request_bytes = 30000000
    server.upstream_read_timeout = 180
    server.queue_retry_delay = 0
    server.default_max_output_tokens = default
    server.use_request_parameters = use_request_parameters
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('streaming', [True, False])
def test_native_length_is_preserved_in_wire_and_saved_response(tmp_path, monkeypatch, streaming):
    with bridge(tmp_path, monkeypatch, 'length') as (server, seen):
        request = Request(f'http://127.0.0.1:{server.server_port}/v1/responses',
            headers={'Authorization': 'Bearer test-only-key'}, data=json.dumps({
                'model': 'traex/GLM-5.4', 'input': 'test', 'stream': streaming}).encode())
        with urlopen(request, timeout=5) as reply:
            wire = reply.read()
        if streaming:
            assert b'event: response.incomplete\n' in wire
            assert b'event: response.completed\n' not in wire
        else:
            assert json.loads(wire)['status'] == 'incomplete'
        assert seen[0]['max_tokens'] == 128000
    saved = json.loads(next(tmp_path.glob('resp_*.response.json')).read_text())
    assert saved['status'] == 'incomplete'
    assert saved['incomplete_details'] == {'reason': 'max_output_tokens'}
    assert saved['usage']['output_tokens'] == 32000


def test_explicit_limit_is_forwarded_and_normal_stop_stays_completed(tmp_path, monkeypatch):
    with bridge(tmp_path, monkeypatch, 'stop') as (server, seen):
        request = Request(f'http://127.0.0.1:{server.server_port}/v1/responses',
            headers={'Authorization': 'Bearer test-only-key'}, data=json.dumps({
                'model': 'traex/GLM-5.4', 'input': 'test', 'max_output_tokens': 1024}).encode())
        with urlopen(request, timeout=5) as reply:
            assert json.load(reply)['status'] == 'completed'
        assert seen[0]['max_tokens'] == 1024


def test_native_parameter_override_is_explicit_and_preserves_requested_effort(tmp_path, monkeypatch):
    with bridge(tmp_path, monkeypatch, 'stop', use_request_parameters=True) as (server, seen):
        request = Request(f'http://127.0.0.1:{server.server_port}/v1/responses',
            headers={'Authorization': 'Bearer test-only-key'}, data=json.dumps({
                'model': 'traex/GLM-5.4', 'input': 'test', 'reasoning': {'effort': 'high'}}).encode())
        with urlopen(request, timeout=5) as reply:
            assert json.load(reply)['status'] == 'completed'
        assert seen[0]['is_preset'] is False
        assert seen[0]['reasoning_effort'] == 'high'
        assert seen[0]['max_tokens'] == 128000
