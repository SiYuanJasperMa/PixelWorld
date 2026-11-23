"""Case-scoped Responses relay with raw wire evidence and an output budget."""
from __future__ import annotations

from contextlib import contextmanager
import hmac
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

from .token_budget import ResponseUsageBudget
from .util import atomic_json

MAX_BODY = 64 * 1024 * 1024
TERMINALS = {'response.completed', 'response.incomplete', 'response.failed'}


@contextmanager
def responses_relay(*, upstream: str, upstream_key: str, client_key: str, model: str,
                    effort: str, wire: Path, budget: ResponseUsageBudget):
    """Forward unchanged bytes to an authorized loopback Responses endpoint.

    The caller owns the case workspace and agent teardown. This relay never
    grants a tool permission. Only provider terminal usage can open or close
    the budget for the next request.
    """
    target = urlsplit(upstream)
    if (target.scheme, target.hostname, target.path) != ('http', '127.0.0.1', '/v1/responses') or (
        not target.port or target.query or target.fragment or target.username or target.password
    ):
        raise ValueError('Expected a trusted loopback Responses endpoint')
    if not upstream_key or not client_key:
        raise ValueError('Both relay and upstream credentials are required')
    wire.mkdir(mode=0o700, exist_ok=False)
    lock = Lock()
    seq = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def error_json(self, status, code):
            body = json.dumps({'error': {'code': code, 'message': code}}).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            nonlocal seq
            auth = self.headers.get('Authorization', '')
            if not hmac.compare_digest(auth, 'Bearer ' + client_key):
                return self.error_json(401, 'relay_auth_required')
            if self.path != '/v1/responses':
                return self.error_json(404, 'route_not_available')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('request size')
                body = self.rfile.read(size)
                obj = json.loads(body)
                if (len(body) != size or not isinstance(obj, dict) or obj.get('model') != model or
                        (obj.get('reasoning') or {}).get('effort') != effort):
                    raise ValueError('model or reasoning mismatch')
            except (ValueError, AttributeError):
                return self.error_json(400, 'request_does_not_match_frozen_route')
            with lock:
                if not budget.can_dispatch:
                    code = 'token_usage_unavailable' if budget.unavailable else 'output_token_budget_exhausted'
                    return self.error_json(400, code)
                seq += 1
                request_dir = wire / f'{seq:04d}'
                request_dir.mkdir(mode=0o700)
                (request_dir/'input.json').write_bytes(body)
                forwarding = HTTPConnection(target.hostname, target.port, timeout=180)
                terminal = None
                status = None
                sent_headers = False
                client_connected = True
                try:
                    forwarding.request('POST', target.path, body=body, headers={
                        'Authorization': 'Bearer ' + upstream_key, 'Content-Type': 'application/json',
                    })
                    with forwarding.getresponse() as reply, (request_dir/'response.raw').open('wb') as saved:
                        status = reply.status
                        mimetype = reply.getheader('Content-Type') or 'application/octet-stream'
                        self.send_response(reply.status)
                        self.send_header('Content-Type', mimetype)
                        self.end_headers()
                        sent_headers = True
                        streaming = mimetype.split(';', 1)[0] == 'text/event-stream'
                        count = 0
                        event = []
                        while True:
                            raw = reply.readline(MAX_BODY + 1) if streaming else reply.read(MAX_BODY + 1)
                            if not raw:
                                break
                            count += len(raw)
                            if count > MAX_BODY:
                                raise ValueError('response too large')
                            saved.write(raw)
                            if client_connected:
                                try:
                                    self.wfile.write(raw)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    client_connected = False
                            if not streaming:
                                if 200 <= status < 300:
                                    value = json.loads(raw)
                                    terminal = value if isinstance(value, dict) else None
                                break
                            if raw.startswith(b'data:'):
                                event.append(raw[5:].lstrip(b' ').rstrip(b'\r\n'))
                            elif not raw.strip() and event:
                                block = b'\n'.join(event)
                                event.clear()
                                if block != b'[DONE]':
                                    item = json.loads(block)
                                    if item.get('type') in TERMINALS:
                                        terminal = item.get('response')
                        if 200 <= status < 300:
                            budget.record(terminal if isinstance(terminal, dict) else {})
                        if status >= 500:
                            budget.record({})
                except (OSError, HTTPException, ValueError, AttributeError) as exc:
                    budget.record({})
                    atomic_json(request_dir/'transport_error.json', {'type': type(exc).__name__})
                    if not sent_headers:
                        self.error_json(502, 'upstream_usage_unavailable')
                finally:
                    forwarding.close()
                    atomic_json(request_dir/'receipt.json', {'upstream_status': status,
                        'response_id': terminal.get('id') if isinstance(terminal, dict) else None,
                        'budget': budget.report()})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False  # Close only after in-flight receipts have been written.
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
