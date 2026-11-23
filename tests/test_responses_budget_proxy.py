from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from pixelworld.responses_budget_proxy import responses_relay
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
