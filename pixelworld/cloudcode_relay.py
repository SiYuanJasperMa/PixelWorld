"""Case-scoped raw Cloud Code recording and external authentication.

Request and response bodies remain unchanged. The relay pins the model,
refuses auxiliary models and non-allowlisted methods, records raw model I/O,
and accounts for candidate plus thought output tokens. Account bootstrap
methods produce size/digest receipts only. When host_access_token is supplied,
the relay replaces the client's placeholder Authorization header outside the
agent filesystem. The HTTP CONNECT proxy is optional and applies upstream.
"""
from __future__ import annotations

from contextlib import contextmanager
import gzip
import hashlib
import os
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import ssl
from threading import Lock, Thread
from urllib.parse import urlsplit

from .token_budget import ResponseUsageBudget
from .util import atomic_bytes, atomic_json, fsync_stream, utc_now, write_stream_bytes

UPSTREAM = 'https://daily-cloudcode-pa.googleapis.com'
SECRET_HEADERS = {'authorization', 'x-api-key', 'x-goog-api-key', 'cookie', 'proxy-authorization'}
HOP_HEADERS = {'host', 'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade',
               'proxy-connection', 'content-length', 'accept-encoding'}
MODEL_METHODS = (':generateContent', ':streamGenerateContent')
# Non-model Cloud Code methods agy 1.5.18 needs at session start (observed);
# every other path is refused locally and recorded.
SESSION_METHODS = ('/v1internal:loadCodeAssist', '/v1internal:fetchAdminControls',
                   '/v1internal:retrieveUserQuotaSummary', '/v1internal:fetchUserInfo',
                   '/v1internal:listExperiments', '/v1internal:fetchAvailableModels')


def forwarded_path(path: str) -> bool:
    route = urlsplit(path).path
    return route in SESSION_METHODS or route in ('/v1internal' + m for m in MODEL_METHODS)


def is_model_call(path: str) -> bool:
    return urlsplit(path).path.endswith(MODEL_METHODS)


def decode_body(raw: bytes, encoding: str | None) -> bytes:
    if encoding and 'gzip' in encoding.lower():
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    return raw


def _response_object(event: dict) -> dict:
    # Cloud Code wraps a GenerateContentResponse as {"response": {...}, "traceId": ...}.
    return event.get('response') if isinstance(event.get('response'), dict) else event


def parse_stream(raw: bytes, content_type: str) -> dict:
    """Return usage, modelVersion, responseId, finishReason of a (streamed) generateContent reply."""
    events = []
    if 'text/event-stream' in content_type or raw.lstrip().startswith(b'data:'):
        for line in raw.splitlines():
            if line.startswith(b'data:'):
                try:
                    events.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
    else:
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        events = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    usage, model, identity, finish, trace = None, None, None, None, None
    for event in events:
        if not isinstance(event, dict):
            continue
        trace = event.get('traceId') or trace
        response = _response_object(event)
        if isinstance(response.get('usageMetadata'), dict):
            usage = response['usageMetadata']
        model = response.get('modelVersion') or model
        identity = response.get('responseId') or identity
        for candidate in response.get('candidates') or []:
            if isinstance(candidate, dict) and candidate.get('finishReason'):
                finish = candidate['finishReason']
    return {'usage': usage, 'model_version': model, 'response_id': identity, 'finish_reason': finish,
            'trace_id': trace, 'events': len(events)}


def output_tokens(usage: dict | None) -> int | None:
    """Reported output tokens of one generation: candidates + thoughts.

    Gemini's ``candidatesTokenCount`` excludes thinking; ``thoughtsTokenCount``
    is billed as output. Their sum equals agy's own ``output_tokens`` (which
    already includes ``thinking_tokens``) and matches Claude, whose
    ``output_tokens`` include thinking.
    """
    if not isinstance(usage, dict):
        return None
    candidates, thoughts = usage.get('candidatesTokenCount', 0), usage.get('thoughtsTokenCount', 0)
    if type(candidates) is not int or type(thoughts) is not int:
        return None
    return candidates + thoughts


@contextmanager
def cloudcode_relay(*, wire: Path, model: str, budget: ResponseUsageBudget, upstream: str = UPSTREAM,
                    timeout: float = 3600, agent_request_types=None, refuse_auxiliary: bool = True,
                    secrets=None, host_access_token=None, proxy_url=None):
    """Yield the loopback base URL for ``agy acp --cloud_code_endpoint``.

    Every model call must name exactly the pinned ``model``. ``agy`` also makes
    auxiliary calls on other models (conversation titles, its tool-call safety
    rater); with ``refuse_auxiliary`` they are refused locally (recorded,
    never forwarded), so only the pinned model is ever used. When
    ``agent_request_types`` is given, a pinned-model call must also carry one
    of those Cloud Code ``requestType`` values. ``secrets()`` returns credential
    strings that must never reach the model: a model call containing one is
    refused and its body is not recorded.
    """
    target = urlsplit(upstream)
    wire.mkdir(mode=0o700, parents=True, exist_ok=False)
    counter = [0]
    counter_lock, model_lock = Lock(), Lock()

    def connection():
        if target.scheme == 'https':
            if proxy_url:
                proxy = urlsplit(proxy_url)
                if proxy.scheme != 'http' or not proxy.hostname:
                    raise ValueError('The upstream proxy must be an HTTP CONNECT proxy')
                conn = http.client.HTTPSConnection(proxy.hostname, proxy.port or 80, timeout=timeout,
                                                   context=ssl.create_default_context())
                conn.set_tunnel(target.hostname, target.port or 443)
                return conn
            return http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(target.hostname, target.port or 80, timeout=timeout)

    class Relay(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)

        def handle_any(self):
            with counter_lock:
                counter[0] += 1
                folder = wire / f'{counter[0]:04d}'
            folder.mkdir(mode=0o700)
            path = self.path
            if path.startswith(('http://', 'https://')):
                split = urlsplit(path)
                path = split.path + ('?' + split.query if split.query else '')
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            model_call = self.command == 'POST' and is_model_call(path)
            receipt = {'method': self.command, 'path': path, 'model_call': model_call,
                       'started_utc': utc_now(), 'response_id': None, 'usage': None,
                       'request_headers': {k: v for k, v in self.headers.items() if k.lower() not in SECRET_HEADERS}}
            leaked = model_call and secrets is not None and any(
                value.encode() in body or value.encode() in decode_body(body, self.headers.get('Content-Encoding'))
                for value in secrets() if value)
            if leaked:
                receipt['request_sha256'] = hashlib.sha256(body).hexdigest()
            elif model_call:
                atomic_bytes(folder / 'input.body', body)
                # Create the response inode before sending the paid request.
                atomic_bytes(folder / 'response.raw', b'')
            else:
                # Session methods (loadCodeAssist, fetchUserInfo, quota, ...) carry account
                # data such as the operator's e-mail: record only their size and digest.
                receipt.update(request_bytes=len(body), request_sha256=hashlib.sha256(body).hexdigest())
                if path.split('?', 1)[0] in ('/v1internal:retrieveUserQuotaSummary', '/v1internal:fetchAvailableModels'):
                    try:
                        parsed = json.loads(body or b'{}')
                    except ValueError:
                        parsed = {}
                    if not isinstance(parsed, dict):
                        parsed = {}
                    receipt['quota_request_keys'] = sorted(parsed)
                    receipt['client_metadata'] = {k: v for k, v in parsed.items()
                        if k in ('clientId', 'clientVersion', 'client', 'userAgent', 'agent')
                        and isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9._/-]{1,100}', v)}
            lock = model_lock if model_call else Lock()
            with lock:
                try:
                    if leaked:
                        receipt['refused'] = 'credential_in_model_request'
                        return self.reply(400, {'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
                            'message': 'PixelWorld relay: request refused'}})
                    if self.command != 'POST' or not forwarded_path(path):
                        receipt['refused'] = 'path_not_allowlisted'
                        return self.reply(403, {'error': {'code': 403, 'status': 'PERMISSION_DENIED',
                            'message': 'PixelWorld relay: Cloud Code method not allowlisted'}})
                    if model_call:
                        try:
                            request = json.loads(decode_body(body, self.headers.get('Content-Encoding')))
                        except ValueError:
                            request = {}
                        requested = request.get('model') if isinstance(request, dict) else None
                        kind = request.get('requestType') if isinstance(request, dict) else None
                        receipt.update(requested_model=requested, request_type=kind)
                        problem = None
                        if requested != model:
                            problem = f'route_mismatch: model {requested!r} is not the pinned {model!r}'
                            if not refuse_auxiliary:
                                problem = None
                                receipt['auxiliary'] = True
                        elif agent_request_types is not None and kind not in agent_request_types:
                            problem = f'route_mismatch: request type {kind!r} is not an agent turn'
                        if problem:
                            receipt['refused'] = problem
                            return self.reply(400, {'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
                                'message': 'PixelWorld relay: ' + problem}})
                        if not budget.can_dispatch and not receipt.get('auxiliary'):
                            receipt['refused'] = 'output_token_budget_exhausted'
                            return self.reply(429, {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED',
                                'message': 'PixelWorld relay: output-token budget exhausted'}})
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                    if host_access_token is not None:
                        # The sandbox contains a non-secret placeholder. Authentication
                        # belongs to the fixed-upstream host relay, never to agent tools.
                        headers = {k: v for k, v in headers.items() if k.lower() != 'authorization'}
                        headers['Authorization'] = 'Bearer ' + host_access_token()
                    headers['Accept-Encoding'] = 'identity'
                    conn = connection()
                    conn.request(self.command, path, body=body or None, headers=headers)
                    upstream_reply = conn.getresponse()
                    receipt['upstream_status'] = upstream_reply.status
                    content_type = upstream_reply.getheader('Content-Type', '')
                    self.send_response(upstream_reply.status)
                    for key, value in upstream_reply.getheaders():
                        if key.lower() not in HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    raw = bytearray()
                    with ((folder / 'response.raw').open('r+b', buffering=0) if model_call else open(os.devnull, 'wb')) as sink:
                        while True:
                            chunk = upstream_reply.read1(65536)
                            if not chunk:
                                break
                            raw.extend(chunk)
                            if model_call:
                                write_stream_bytes(sink, chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        if model_call:
                            fsync_stream(sink)
                    conn.close()
                    if not model_call:
                        receipt.update(response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest())
                    if model_call and upstream_reply.status == 200:
                        parsed = parse_stream(bytes(raw), content_type)
                        tokens = output_tokens(parsed['usage'])
                        identity = parsed['response_id'] or parsed['trace_id'] or f'{folder.name}'
                        receipt.update(response_id=identity, usage=parsed['usage'], model_version=parsed['model_version'],
                                       finish_reason=parsed['finish_reason'], output_tokens=tokens,
                                       stream_events=parsed['events'])
                        if not receipt.get('auxiliary'):
                            # Auxiliary (non-pinned) calls are recorded but not charged to the case budget.
                            budget.record({'id': f'{folder.name}:{identity}',
                                           'status': 'incomplete' if parsed['finish_reason'] == 'MAX_TOKENS' else 'completed',
                                           'usage': {'output_tokens': tokens}})
                except (BrokenPipeError, ConnectionResetError) as error:
                    receipt['transport_error'] = type(error).__name__
                    if model_call and receipt.get('upstream_status') == 200 and receipt['usage'] is None:
                        budget.record({'id': None})
                except Exception as error:
                    receipt['transport_error'] = type(error).__name__
                    receipt['transport_error_message'] = str(error)[:300]
                    try:
                        self.reply(502, {'error': {'code': 502, 'message': 'PixelWorld relay transport failure'}})
                    except Exception:
                        pass
                finally:
                    receipt['completed_utc'] = utc_now()
                    atomic_json(folder / 'receipt.json', receipt)
                    self.close_connection = True

        do_POST = do_GET = do_PUT = do_DELETE = do_PATCH = handle_any

    server = ThreadingHTTPServer(('127.0.0.1', 0), Relay)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
