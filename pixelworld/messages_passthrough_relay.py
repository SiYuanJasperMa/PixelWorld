"""Case-scoped verbatim relay between Claude Code and the Anthropic API.

Used when the pinned Claude Code client authenticates with the operator's own
Claude subscription. The relay never rewrites a request body or a response:
Claude Code's own body and non-credential headers are forwarded unchanged and
the upstream bytes are streamed back unchanged. It only

* optionally injects the upstream credential (``upstream_authorization``):
  when set, any client ``Authorization``/``x-api-key`` header is dropped and
  ``Authorization: Bearer <token>`` is sent upstream instead. This keeps the
  real OAuth token outside a sandboxed client, which only holds a
  placeholder. Without it the client's own credential headers pass through;
* records each exchange (method, path, request body, raw response bytes, a
  receipt with the upstream request id and parsed usage) under ``wire/NNNN/``;
  credential headers and the injected token are never written;
* serializes this case's model requests and applies the shared after-request
  output-token budget: once the budget is crossed, further ``/v1/messages``
  calls are refused locally without an outbound request;
* refuses a ``/v1/messages`` request whose model or requested effort differs
  from the pinned route, so a silent model substitution cannot occur.

Other paths (e.g. ``/v1/messages/count_tokens`` or ``/api/...``) are forwarded
with the same credential handling, recorded with ``model_call: false`` and not
counted as model calls or against the budget.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
from threading import Lock, Thread
from urllib.parse import urlsplit

from .token_budget import ResponseUsageBudget
from .util import atomic_json, utc_now

SECRET_HEADERS = {'authorization', 'x-api-key', 'cookie', 'proxy-authorization'}
HOP_HEADERS = {'host', 'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade',
               'proxy-connection', 'content-length', 'accept-encoding'}
MODEL_PATHS = ('/v1/messages',)


def is_model_call(path: str) -> bool:
    return urlsplit(path).path in MODEL_PATHS


def parse_usage(raw: bytes, content_type: str) -> tuple[dict | None, str | None, str | None]:
    """Return (usage, message id, stop reason) from a Messages body or SSE stream."""
    if 'text/event-stream' not in content_type:
        try:
            body = json.loads(raw)
        except ValueError:
            return None, None, None
        return body.get('usage'), body.get('id'), body.get('stop_reason')
    usage, identity, stop, terminal = {}, None, None, False
    for line in raw.splitlines():
        if not line.startswith(b'data:'):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = event.get('type')
        if kind == 'message_start':
            message = event.get('message') or {}
            identity = message.get('id')
            usage.update({k: v for k, v in (message.get('usage') or {}).items() if v is not None})
        elif kind == 'message_delta':
            stop = (event.get('delta') or {}).get('stop_reason') or stop
            usage.update({k: v for k, v in (event.get('usage') or {}).items() if v is not None})
        elif kind == 'message_stop':
            terminal = True
    return (usage if terminal else None), identity, stop


def route_mismatch(body: dict, model: str, effort: str | None) -> str | None:
    if body.get('model') != model:
        return f"model {body.get('model')!r} is not the pinned {model!r}"
    if effort is not None:
        requested = (body.get('output_config') or {}).get('effort')
        if requested != effort:
            return f'effort {requested!r} is not the pinned {effort!r}'
    return None


@contextmanager
def messages_relay(*, wire: Path, model: str, effort: str | None, budget: ResponseUsageBudget,
                   upstream: str = 'https://api.anthropic.com', timeout: float = 3600,
                   upstream_authorization: str | None = None):
    """Yield the loopback base URL for ``ANTHROPIC_BASE_URL``.

    ``upstream_authorization`` is a bearer token substituted for the client's
    credential headers on every outbound request; it is never recorded.
    """
    # A callable is resolved per request so a long case follows host login refreshes.
    if upstream_authorization is not None and not callable(upstream_authorization) and (
            not upstream_authorization or any(c in upstream_authorization for c in '\r\n')):
        raise ValueError('Invalid upstream authorization token')
    target = urlsplit(upstream)
    wire.mkdir(mode=0o700, parents=True, exist_ok=False)
    counter = [0]
    counter_lock, model_lock = Lock(), Lock()

    def connection():
        if target.scheme == 'https':
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
            self.end_headers()
            self.wfile.write(data)

        def handle_any(self):
            with counter_lock:
                counter[0] += 1
                folder = wire / f'{counter[0]:04d}'
            folder.mkdir(mode=0o700)
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            model_call = self.command == 'POST' and is_model_call(self.path)
            receipt = {'method': self.command, 'path': self.path, 'model_call': model_call,
                       'started_utc': utc_now(), 'response_id': None, 'usage': None,
                       'request_headers': {k: v for k, v in self.headers.items() if k.lower() not in SECRET_HEADERS}}
            (folder / 'input.body').write_bytes(body)
            lock = model_lock if model_call else Lock()
            with lock:
                try:
                    if model_call:
                        try:
                            request = json.loads(body)
                        except ValueError:
                            request = {}
                        receipt.update(model=request.get('model'),
                                       effort=(request.get('output_config') or {}).get('effort'),
                                       stream=request.get('stream') is True)
                        problem = route_mismatch(request, model, effort)
                        if problem:
                            receipt['refused'] = 'route_mismatch: ' + problem
                            return self.reply(400, {'type': 'error', 'error': {
                                'type': 'invalid_request_error', 'message': 'PixelWorld relay: ' + problem}})
                        if not budget.can_dispatch:
                            receipt['refused'] = 'output_token_budget_exhausted'
                            return self.reply(400, {'type': 'error', 'error': {
                                'type': 'invalid_request_error',
                                'message': 'PixelWorld relay: output-token budget exhausted'}})
                    if upstream_authorization is not None and not (self.command == 'POST' and urlsplit(self.path).path in
                            ('/v1/messages', '/v1/messages/count_tokens')):
                        # The injected credential is only ever spent on pinned, budgeted model calls.
                        receipt['refused'] = 'non_model_path_with_injected_credential'
                        return self.reply(403, {'type': 'error', 'error': {'type': 'permission_error',
                            'message': 'PixelWorld relay: only POST /v1/messages[/count_tokens] is forwarded'}})
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                    if upstream_authorization is not None:
                        headers = {k: v for k, v in headers.items() if k.lower() not in ('authorization', 'x-api-key')}
                        credential = upstream_authorization() if callable(upstream_authorization) else upstream_authorization
                        if not credential or any(c in credential for c in '\r\n'):
                            raise ValueError('invalid upstream credential')
                        headers['Authorization'] = 'Bearer ' + credential
                        receipt['credential'] = 'injected by relay (value not recorded)'
                    headers['Accept-Encoding'] = 'identity'
                    conn = connection()
                    conn.request(self.command, self.path, body=body or None, headers=headers)
                    upstream_reply = conn.getresponse()
                    receipt['upstream_status'] = upstream_reply.status
                    receipt['upstream_request_id'] = upstream_reply.getheader('request-id')
                    content_type = upstream_reply.getheader('Content-Type', '')
                    self.send_response(upstream_reply.status)
                    for key, value in upstream_reply.getheaders():
                        if key.lower() not in HOP_HEADERS:
                            self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    raw = bytearray()
                    with (folder / 'response.raw').open('wb') as sink:
                        while True:
                            chunk = upstream_reply.read1(65536)
                            if not chunk:
                                break
                            raw.extend(chunk)
                            sink.write(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    conn.close()
                    if model_call and upstream_reply.status == 200:
                        usage, identity, stop = parse_usage(bytes(raw), content_type)
                        receipt.update(response_id=identity, usage=usage, stop_reason=stop)
                        status = 'incomplete' if stop in ('max_tokens', 'refusal') else 'completed'
                        budget.record({'id': identity, 'status': status,
                                       'usage': {'output_tokens': (usage or {}).get('output_tokens')}})
                except (BrokenPipeError, ConnectionResetError) as error:
                    receipt['transport_error'] = type(error).__name__
                    if model_call and receipt.get('upstream_status') == 200 and receipt['usage'] is None:
                        budget.record({'id': None})
                except Exception as error:
                    receipt['transport_error'] = type(error).__name__
                    receipt['transport_error_message'] = str(error)[:300]
                    try:
                        self.reply(502, {'type': 'error', 'error': {'type': 'api_error',
                                                                     'message': 'PixelWorld relay transport failure'}})
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


def wire_items(request: dict) -> list[dict]:
    """Project Messages tool_use/tool_result blocks as Responses-style tool items."""
    items = []
    for message in request.get('messages', []):
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                items.append({'type': 'function_call', 'call_id': block.get('id'), 'name': block.get('name'),
                              'arguments': json.dumps(block.get('input'), ensure_ascii=False)})
            elif block.get('type') == 'tool_result':
                value = block.get('content', '')
                if isinstance(value, list):
                    value = '\n'.join(part.get('text', '') for part in value if isinstance(part, dict))
                items.append({'type': 'function_call_output', 'call_id': block.get('tool_use_id'), 'output': value})
    return items


def responses_usage(usage: dict) -> dict:
    fresh = usage.get('input_tokens') or 0
    read = usage.get('cache_read_input_tokens') or 0
    write = usage.get('cache_creation_input_tokens') or 0
    return {'input_tokens': fresh + read + write, 'output_tokens': usage.get('output_tokens') or 0,
            'total_tokens': fresh + read + write + (usage.get('output_tokens') or 0),
            'input_tokens_details': {'cached_tokens': read, 'cache_write_tokens': write}}
