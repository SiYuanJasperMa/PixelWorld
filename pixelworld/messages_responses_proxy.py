"""Native Messages frontend for a case-scoped, budgeted Responses relay."""
from __future__ import annotations

from contextlib import contextmanager
import hmac
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from .messages_responses_adapter import to_messages, to_responses
from .responses_budget_proxy import MAX_BODY
from .util import atomic_json


def encode_stream(reply):
    events = [('message_start', {'message': {**reply, 'content': [], 'stop_reason': None,
                 'usage': {**reply['usage'], 'output_tokens': 0}}})]
    for index, part in enumerate(reply['content']):
        if part['type'] == 'tool_use':
            first = {**part, 'input': {}}
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(part['input'])}
        else:
            first = {**part, 'text': ''}
            delta = {'type': 'text_delta', 'text': part['text']}
        events.extend([('content_block_start', {'index': index, 'content_block': first}),
                       ('content_block_delta', {'index': index, 'delta': delta}),
                       ('content_block_stop', {'index': index})])
    events.extend([('message_delta', {'delta': {'stop_reason': reply['stop_reason'], 'stop_sequence': None},
                                     'usage': {'output_tokens': reply['usage']['output_tokens']}}),
                   ('message_stop', {})])
    return ''.join(f'event: {kind}\ndata: {json.dumps({"type": kind, **data})}\n\n'
                   for kind, data in events).encode()


@contextmanager
def messages_frontend(*, responses_base: str, responses_key: str, client_key: str,
                      model: str, effort: str, wire: Path, allowed_tools: frozenset[str]):
    target = urlsplit(responses_base)
    if (target.scheme, target.hostname, target.path) != ('http', '127.0.0.1', '/v1') or (
        not target.port or target.query or target.fragment or target.username or target.password
    ):
        raise ValueError('Expected the local Responses budget relay')
    if not responses_key or not client_key:
        raise ValueError('Both local credentials are required')
    wire.mkdir(mode=0o700, exist_ok=False)
    lock = Lock()
    seq = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def emit(self, status, content, mime='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def reject(self, status, label):
            kind = 'rate_limit_error' if status == 429 else 'invalid_request_error' if status < 500 else 'api_error'
            self.emit(status, json.dumps({'type': 'error', 'error': {'type': kind, 'message': label}}).encode())

        def do_POST(self):
            nonlocal seq
            auth = self.headers.get('Authorization', '').removeprefix('Bearer ') or self.headers.get('x-api-key', '')
            if not hmac.compare_digest(auth, client_key):
                return self.reject(401, 'message_auth_required')
            if self.path.split('?', 1)[0] != '/v1/messages':
                return self.reject(404, 'route_not_available')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('invalid body length')
                raw = self.rfile.read(size)
                request = json.loads(raw)
                if len(raw) != size or not isinstance(request, dict):
                    raise ValueError('incomplete native body')
                names = {tool['name'] for tool in request.get('tools', [])}
                if not names <= allowed_tools:
                    raise ValueError('tool outside the case allowlist')
                translated = to_responses(request, model=model, reasoning_effort=effort)
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.reject(400, 'native_request_not_supported')
            with lock:
                seq += 1
                directory = wire/f'{seq:04d}'
                directory.mkdir(mode=0o700)
                (directory/'input.json').write_bytes(raw)
                conn = HTTPConnection(target.hostname, target.port, timeout=190)
                try:
                    conn.request('POST', '/v1/responses', body=json.dumps(translated).encode(), headers={
                        'Authorization': 'Bearer ' + responses_key, 'Content-Type': 'application/json',
                    })
                    with conn.getresponse() as upstream:
                        body = upstream.read(MAX_BODY+1)
                        if len(body) > MAX_BODY:
                            raise ValueError('response too large')
                        if upstream.status != 200:
                            label = 'upstream_unavailable'
                            try:
                                code = json.loads(body).get('error', {}).get('code')
                                if code in ('output_token_budget_exhausted', 'token_usage_unavailable'):
                                    label = code
                            except (ValueError, AttributeError):
                                pass
                            atomic_json(directory/'receipt.json', {'status': upstream.status, 'error': label})
                            return self.reject(upstream.status, label)
                    result = to_messages(json.loads(body), model=model)
                    if any(part['type'] == 'tool_use' and part['name'] not in allowed_tools for part in result['content']):
                        raise ValueError('provider tool outside the case allowlist')
                    streaming = request.get('stream') is True
                    output = encode_stream(result) if streaming else json.dumps(result).encode()
                    (directory/'response.raw').write_bytes(output)
                    atomic_json(directory/'receipt.json', {'status': 200, 'response_id': result['id'],
                                                          'projected_usage': result['usage']})
                except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError) as error:
                    atomic_json(directory/'receipt.json', {'status': 502, 'error_type': type(error).__name__})
                    return self.reject(502, 'native_translation_failed')
                finally:
                    conn.close()
                self.emit(200, output, 'text/event-stream' if streaming else 'application/json')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
