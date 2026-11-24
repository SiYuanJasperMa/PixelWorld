from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from pixelworld.responses_budget_proxy import responses_relay
from pixelworld.responses_budget_proxy import transport_failure_evidence
from pixelworld.token_budget import ResponseUsageBudget


@contextmanager
def fake_provider(responses):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_POST(self):
            seen.append(self.rfile.read(int(self.headers['Content-Length'])))
            status, content_type, body = responses[len(seen) - 1]
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1/responses', seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def call(url, raw):
    request = Request(url + '/responses', data=raw, headers={'Authorization': 'Bearer test-relay'})
    with urlopen(request, timeout=5) as response:
        return response.read()


def relay(upstream, path, budget):
    return responses_relay(upstream=upstream, upstream_key='test-upstream', client_key='test-relay',
                           model='pinned-model', effort='high', wire=path, budget=budget)


BODY = b'{"model":"pinned-model","reasoning":{"effort":"high"},"stream":true}'


def test_stream_is_byte_exact_and_next_request_does_not_reach_upstream(tmp_path):
    terminal = {'type': 'response.completed', 'response': {'id': 'test-response', 'status': 'completed',
                'usage': {'output_tokens': 88}}}
    stream = b': keepalive\n\n' + ('event: response.completed\ndata: ' + json.dumps(terminal) + '\n\n').encode()
    budget = ResponseUsageBudget(64)
    wire = tmp_path/'wire'
    with fake_provider([(200, 'text/event-stream', stream)]) as (up, seen), relay(up, wire, budget) as url:
        assert call(url, BODY) == stream
        with pytest.raises(HTTPError) as blocked:
            call(url, BODY)
        assert blocked.value.code == 400
        assert json.load(blocked.value)['error']['code'] == 'output_token_budget_exhausted'
        assert seen == [BODY]
        assert (wire/'0001/input.json').read_bytes() == BODY
        assert (wire/'0001/response.raw').read_bytes() == stream
        receipt = json.loads((wire/'0001/receipt.json').read_text())
        assert receipt['budget']['observed_overshoot_tokens'] == 24


def test_rate_limit_can_retry_but_missing_usage_blocks(tmp_path):
    responses = [(429, 'application/json', b'{"error":"rate limit"}'),
                 (200, 'text/event-stream', b': interrupted without terminal usage\n\n')]
    budget = ResponseUsageBudget(64)
    with fake_provider(responses) as (up, seen), relay(up, tmp_path/'wire', budget) as url:
        with pytest.raises(HTTPError) as busy:
            call(url, BODY)
        assert busy.value.code == 429
        assert budget.can_dispatch
        assert b'interrupted' in call(url, BODY)
        with pytest.raises(HTTPError) as blocked:
            call(url, BODY)
        assert json.load(blocked.value)['error']['code'] == 'token_usage_unavailable'
        assert seen == [BODY, BODY]


def test_upstream_connection_has_no_read_deadline(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    seen_timeouts = []
    def connect(*args, **kwargs):
        seen_timeouts.append(kwargs.get('timeout'))
        return HTTPConnection(*args, **kwargs)
    monkeypatch.setattr('pixelworld.responses_budget_proxy.HTTPConnection', connect)
    response = b'{"id":"r","status":"completed","usage":{"output_tokens":3}}'
    with fake_provider([(200, 'application/json', response)]) as (up, _), relay(up, tmp_path/'wire', ResponseUsageBudget(64)) as url:
        assert call(url, BODY) == response
    assert seen_timeouts == [None]


def test_teardown_interrupts_an_upstream_wait_and_keeps_unknown_usage(tmp_path):
    entered, release = Event(), Event()
    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            release.wait(10)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            except OSError: pass
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    serving = Thread(target=upstream.serve_forever, daemon=True)
    serving.start()
    budget = ResponseUsageBudget(64)
    context = relay(f'http://127.0.0.1:{upstream.server_port}/v1/responses', tmp_path/'wire', budget)
    url = context.__enter__()
    results = []
    def client():
        try: results.append(call(url, BODY))
        except (HTTPError, OSError): pass
    reader = Thread(target=client)
    reader.start()
    closing = Thread(target=lambda: context.__exit__(None, None, None))
    try:
        assert entered.wait(2)
        closing.start()
        closing.join(2)
        assert not closing.is_alive(), 'Relay teardown must cancel its blocking socket'
        assert budget.report()['reported_output_tokens'] is None
        error = json.loads((tmp_path/'wire/0001/transport_error.json').read_text())
        assert error['origin'] == 'relay_shutdown'
        assert transport_failure_evidence(tmp_path/'wire', model='pinned-model', effort='high') is None
    finally:
        release.set()
        reader.join(6)
        closing.join(6)
        upstream.shutdown(); upstream.server_close(); serving.join()


def test_incomplete_stream_is_evidence_but_completed_missing_usage_is_not(tmp_path):
    for label, content_type, body in [('stream', 'text/event-stream', b': closed before terminal\n\n'),
        ('complete', 'application/json', b'{"id":"r","status":"completed","usage":null}')]:
        wire = tmp_path/label
        budget = ResponseUsageBudget(64)
        with fake_provider([(200, content_type, body)]) as (up, _), relay(up, wire, budget) as url:
            assert call(url, BODY) == body
        evidence = transport_failure_evidence(wire, model='pinned-model', effort='high')
        assert bool(evidence) == (label == 'stream')
        assert budget.unavailable and budget.report()['reported_output_tokens'] is None
        if evidence:
            assert evidence['failures'][0]['type'] == 'StreamDisconnectedBeforeTerminal'


def test_retry_after_header_is_preserved_without_closing_the_budget(tmp_path):
    class Limited(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(429); self.send_header('Retry-After', '30'); self.end_headers()
            self.wfile.write(b'{"error":"busy"}')
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Limited)
    worker = Thread(target=provider.serve_forever, daemon=True); worker.start()
    budget = ResponseUsageBudget(64)
    try:
        with relay(f'http://127.0.0.1:{provider.server_port}/v1/responses', tmp_path/'wire', budget) as url:
            with pytest.raises(HTTPError) as busy: call(url, BODY)
            assert busy.value.headers['Retry-After'] == '30'
            assert budget.can_dispatch
    finally:
        provider.shutdown(); provider.server_close(); worker.join()
