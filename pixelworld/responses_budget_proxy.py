"""Case-scoped Responses relay with raw wire evidence and an output budget."""
from __future__ import annotations

from contextlib import contextmanager
import hmac
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

from .token_budget import ResponseUsageBudget
from .util import atomic_json, sha256, utc_now

MAX_BODY = 64 * 1024 * 1024  # Compatibility guard used by the Messages frontend.
TERMINALS = {'response.completed', 'response.incomplete', 'response.failed'}
TRANSPORT_TYPES = {'ConnectionResetError', 'ConnectionAbortedError', 'ConnectionRefusedError',
                   'BrokenPipeError', 'TimeoutError', 'RemoteDisconnected', 'IncompleteRead',
                   'StreamDisconnectedBeforeTerminal'}


def transport_failure_evidence(wire: Path, *, model: str, effort: str):
    """Bind an observed interruption to the preserved request and receipt bytes.

    A completed response that merely omits usage does not qualify. Neither do
    assistant text, a missing file, or an operator-cancelled relay.
    """
    failures = []
    for receipt_path in sorted(wire.glob('[0-9]*/receipt.json')):
        directory = receipt_path.parent
        request_path, error_path, raw_path = directory/'input.json', directory/'transport_error.json', directory/'response.raw'
        try:
            request = json.loads(request_path.read_text())
            receipt = json.loads(receipt_path.read_text())
            if (request.get('model') != model or (request.get('reasoning') or {}).get('effort') != effort
                or receipt.get('usage') is not None or receipt.get('response_id') is not None):
                continue
            error = json.loads(error_path.read_text()) if error_path.is_file() else {}
            kind = error.get('type')
            if error.get('origin') == 'relay_shutdown':
                continue
            if kind not in TRANSPORT_TYPES or kind == 'StreamDisconnectedBeforeTerminal':
                if ((error and kind != 'StreamDisconnectedBeforeTerminal')
                    or not (request.get('stream') is True or error.get('streaming') is True)
                    or receipt.get('upstream_status') != 200 or not raw_path.is_file()):
                    continue
                raw = raw_path.read_bytes()
                if not raw or not any(line.startswith((b'data:', b'event:', b':')) for line in raw.splitlines()):
                    continue
                terminal, malformed, block = False, False, []
                for line in raw.splitlines() + [b'']:
                    if line.startswith(b'data:'):
                        block.append(line[5:].strip())
                    elif not line.strip() and block:
                        data = b'\n'.join(block)
                        block.clear()
                        if data == b'[DONE]':
                            continue
                        try:
                            item = json.loads(data)
                            terminal |= isinstance(item, dict) and item.get('type') in TERMINALS
                        except ValueError:
                            malformed = True
                if terminal or malformed:
                    continue
                kind = 'StreamDisconnectedBeforeTerminal'
            proof = {'request_directory': directory.name, 'type': kind,
                     'request_sha256': sha256(request_path), 'receipt_sha256': sha256(receipt_path),
                     'upstream_status': receipt.get('upstream_status')}
            if error_path.is_file():
                proof['transport_error_sha256'] = sha256(error_path)
            if raw_path.is_file():
                proof['response_sha256'] = sha256(raw_path)
            failures.append(proof)
        except (OSError, ValueError, AttributeError):
            continue
    return {'schema': 'pixelworld-transport-failure-v1', 'model': model, 'reasoning_effort': effort,
            'wire_directory': 'logs/native/responses_wire', 'failures': failures} if failures else None


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
    connection_lock = Lock()
    cancelled = Event()
    sockets = set()
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
                if size <= 0:
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
                forwarding = HTTPConnection(target.hostname, target.port, timeout=None)
                upstream_socket = None
                terminal = None
                status = None
                sent_headers = False
                client_connected = True
                streaming = False
                count = 0
                try:
                    forwarding.connect()
                    upstream_socket = forwarding.sock
                    with connection_lock:
                        if cancelled.is_set():
                            raise ConnectionAbortedError('relay shutdown')
                        sockets.add(upstream_socket)
                    forwarding.request('POST', target.path, body=body, headers={
                        'Authorization': 'Bearer ' + upstream_key, 'Content-Type': 'application/json',
                    })
                    with forwarding.getresponse() as reply, (request_dir/'response.raw').open('wb') as saved:
                        status = reply.status
                        mimetype = reply.getheader('Content-Type') or 'application/octet-stream'
                        self.send_response(reply.status)
                        self.send_header('Content-Type', mimetype)
                        for key, value in reply.getheaders():
                            if key.lower() in ('content-length', 'retry-after', 'x-request-id') or key.lower().startswith('x-ratelimit-'):
                                self.send_header(key, value)
                        self.end_headers()
                        sent_headers = True
                        streaming = mimetype.split(';', 1)[0] == 'text/event-stream'
                        count = 0
                        event = []
                        while True:
                            raw = reply.readline() if streaming else reply.read()
                            if not raw:
                                break
                            count += len(raw)
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
                                        budget.record(terminal if isinstance(terminal, dict) else {})
                        if 200 <= status < 300:
                            if streaming and terminal is None:
                                atomic_json(request_dir/'transport_error.json', {'type': 'StreamDisconnectedBeforeTerminal',
                                    'origin': 'upstream', 'streaming': True, 'upstream_status': status,
                                    'received_bytes': count, 'terminal_received': False})
                            budget.record(terminal if isinstance(terminal, dict) else {})
                        if status >= 500:
                            budget.record({})
                except (OSError, HTTPException, ValueError, AttributeError) as exc:
                    budget.record(terminal if isinstance(terminal, dict) else {})
                    atomic_json(request_dir/'transport_error.json', {'type': type(exc).__name__,
                        'origin': 'relay_shutdown' if cancelled.is_set() else 'upstream',
                        'streaming': streaming, 'upstream_status': status, 'received_bytes': count,
                        'terminal_received': terminal is not None})
                    if not sent_headers:
                        try:
                            self.error_json(502, 'upstream_usage_unavailable')
                        except OSError:
                            pass
                finally:
                    with connection_lock:
                        sockets.discard(upstream_socket)
                    forwarding.close()
                    atomic_json(request_dir/'receipt.json', {'upstream_status': status,
                        'response_id': terminal.get('id') if isinstance(terminal, dict) else None,
                        'usage': terminal.get('usage') if isinstance(terminal, dict) else None,
                        'completed_utc': utc_now(), 'budget': budget.report()})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False  # Close only after in-flight receipts have been written.
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        cancelled.set()
        with connection_lock:
            for active in list(sockets):
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active.close()
        server.shutdown()
        server.server_close()
        thread.join()
