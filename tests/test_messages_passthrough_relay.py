import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pixelworld.messages_passthrough_relay import messages_relay, parse_usage, wire_items
from pixelworld.token_budget import ResponseUsageBudget


def stream(output_tokens, message_id='msg_1', stop='tool_use'):
    events = [{'type': 'message_start', 'message': {'id': message_id, 'model': 'claude-opus-5-5', 'usage': {
                  'input_tokens': 5, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20,
                  'output_tokens': 1}}},
              {'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': {'output_tokens': output_tokens}},
              {'type': 'message_stop'}]
    return ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class Upstream:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                owner.seen.append((self.path, dict(self.headers), body))
                payload = owner.replies.pop(0)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('request-id', 'req_up')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{self.server.server_port}'

    def __exit__(self, *args):
        self.server.shutdown()


def call(base, body, path='/v1/messages?beta=true'):
    data = json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, headers={
        'Authorization': 'Bearer fake-oauth-SECRET', 'anthropic-beta': 'oauth-2025-04-20',
        'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


BODY = {'model': 'claude-opus-5-5', 'stream': True, 'output_config': {'effort': 'high'}, 'max_tokens': 10,
        'messages': [{'role': 'user', 'content': 'hi'}]}


def test_relay_is_verbatim_records_usage_and_never_logs_credentials(tmp_path):
    budget = ResponseUsageBudget(1000)
    payload = stream(40)
    with Upstream([payload]) as upstream:
        with messages_relay(wire=tmp_path / 'wire', model='claude-opus-5-5', effort='high', budget=budget,
                            upstream=upstream) as relay:
            status, body = call(relay, BODY)
    assert status == 200 and body == payload
    receipt = json.loads((tmp_path / 'wire/0001/receipt.json').read_text())
    assert receipt['response_id'] == 'msg_1' and receipt['upstream_request_id'] == 'req_up'
    assert receipt['usage']['output_tokens'] == 40 and receipt['usage']['cache_creation_input_tokens'] == 20
    assert budget.output == 40
    stored = b''.join(p.read_bytes() for p in (tmp_path / 'wire').rglob('*') if p.is_file())
    assert b'SECRET' not in stored
    assert (tmp_path / 'wire/0001/input.body').read_bytes() == json.dumps(BODY).encode()


def test_relay_refuses_other_models_effort_and_exhausted_budget(tmp_path):
    budget = ResponseUsageBudget(30)
    with Upstream([stream(40, 'msg_a')]) as base:
        with messages_relay(wire=tmp_path / 'wire', model='claude-opus-5-5', effort='high', budget=budget,
                            upstream=base) as relay:
            assert call(relay, {**BODY, 'model': 'claude-haiku-5-5'})[0] == 400
            assert call(relay, {**BODY, 'output_config': {'effort': 'medium'}})[0] == 400
            assert call(relay, BODY)[0] == 200
            status, body = call(relay, BODY)
    assert status == 400 and b'budget exhausted' in body
    refused = [json.loads(p.read_text()).get('refused') for p in sorted((tmp_path / 'wire').glob('*/receipt.json'))]
    assert refused[0].startswith('route_mismatch') and refused[1].startswith('route_mismatch')
    assert refused[2] is None and refused[3] == 'output_token_budget_exhausted'
    assert budget.report()['exhausted'] and budget.report()['observed_overshoot_tokens'] == 10


def test_upstream_sees_client_headers(tmp_path):
    upstream = Upstream([stream(1)])
    with upstream as base:
        with messages_relay(wire=tmp_path / 'wire', model='claude-opus-5-5', effort='high',
                            budget=ResponseUsageBudget(1000), upstream=base) as relay:
            call(relay, BODY)
    path, headers, body = upstream.seen[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert path == '/v1/messages?beta=true'
    assert headers['authorization'] == 'Bearer fake-oauth-SECRET'
    assert headers['anthropic-beta'] == 'oauth-2025-04-20'
    assert json.loads(body) == BODY


def test_truncated_stream_marks_usage_unavailable():
    usage, identity, _ = parse_usage(stream(5)[:-30], 'text/event-stream')
    assert usage is None and identity == 'msg_1'


def test_wire_items_project_tool_calls():
    request = {'messages': [{'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': 't1', 'name': 'mcp__pixelworld__bash', 'input': {'command': 'ls'}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1',
                                      'content': [{'type': 'text', 'text': 'a.py'}]}]}]}
    items = wire_items(request)
    assert items[0]['type'] == 'function_call' and json.loads(items[0]['arguments']) == {'command': 'ls'}
    assert items[1] == {'type': 'function_call_output', 'call_id': 't1', 'output': 'a.py'}


def test_relay_injects_upstream_credential_and_never_records_it(tmp_path):
    upstream = Upstream([stream(7), stream(3, 'msg_2')])
    with upstream as base:
        with messages_relay(wire=tmp_path / 'wire', model='claude-opus-5-5', effort='high',
                            budget=ResponseUsageBudget(1000), upstream=base,
                            upstream_authorization='fake-oauth-REALTOKEN') as relay:
            status, body = call(relay, BODY)
            data = json.dumps({'messages': []}).encode()
            request = urllib.request.Request(relay + '/v1/messages/count_tokens?beta=true', data=data, headers={
                'x-api-key': 'placeholder', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as reply:
                assert reply.status == 200
    assert status == 200 and body == stream(7)
    for path, headers, sent in upstream.seen:
        lowered = {k.lower(): v for k, v in headers.items()}
        assert lowered['authorization'] == 'Bearer fake-oauth-REALTOKEN'
        assert 'x-api-key' not in lowered
    assert {k.lower(): v for k, v in upstream.seen[0][1].items()}['anthropic-beta'] == 'oauth-2025-04-20'
    assert json.loads(upstream.seen[0][2]) == BODY
    receipts = [json.loads(p.read_text()) for p in sorted((tmp_path / 'wire').glob('*/receipt.json'))]
    assert [r['model_call'] for r in receipts] == [True, False]
    assert receipts[1]['path'] == '/v1/messages/count_tokens?beta=true' and receipts[1]['usage'] is None
    assert all(r['credential'].startswith('injected') for r in receipts)
    stored = b''.join(p.read_bytes() for p in (tmp_path / 'wire').rglob('*') if p.is_file())
    assert b'REALTOKEN' not in stored and b'SECRET' not in stored


def test_relay_rejects_header_injection_in_credential(tmp_path):
    with pytest.raises(ValueError):
        with messages_relay(wire=tmp_path / 'wire', model='m', effort=None, budget=ResponseUsageBudget(1),
                            upstream_authorization='a\r\nX-Evil: 1'):
            pass
