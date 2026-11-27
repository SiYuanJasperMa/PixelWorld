"""Loopback Responses endpoint for Codex backed by the Anthropic Messages API.

Codex 0.169.0 speaks only the Responses wire. This bridge lets the frozen
Codex harness drive a Claude model without changing the agent loop:

* each Responses request is translated statelessly into one streamed
  ``POST /v1/messages`` call (no ``previous_response_id``);
* Claude thinking blocks (with signatures) travel back to Codex inside the
  reasoning item's ``encrypted_content`` and are restored verbatim on the next
  request, as the Messages API requires for preserved thinking;
* usage is reported as Responses usage: ``input_tokens`` includes cache reads
  and cache writes; ``cached_tokens`` is the cache-read share and
  ``cache_write_tokens`` the cache-write share (both read by Codex 0.169.0's
  ResponseCompletedInputTokensDetails). Reasoning tokens
  are not reported separately by the Messages API and are recorded as
  unavailable in the request log (the wire field is 0);
* no server-side model fallback is requested: a refusal ends the response as
  ``incomplete`` instead of silently substituting another model;
* adapter retries are zero. Upstream HTTP 429/5xx statuses are returned to
  Codex unchanged so its own configured retry policy applies.

Raw Codex input, the exact Messages request, raw upstream SSE bytes and the
final Responses object are preserved per request under ``--log-dir``.
The Anthropic credential is read by the SDK from the proxy's environment and is
never written to the logs.
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ADAPTER = 'anthropic-messages-v1'
REASONING_PREFIX = 'pwanthropic1:'
EFFORTS = {'minimal': 'low', 'low': 'low', 'medium': 'medium', 'high': 'high',
           'xhigh': 'xhigh', 'max': 'max'}
STOP_STATUS = {'end_turn': ('completed', None), 'tool_use': ('completed', None),
               'stop_sequence': ('completed', None),
               'max_tokens': ('incomplete', {'reason': 'max_output_tokens'}),
               'model_context_window_exceeded': ('incomplete', {'reason': 'max_output_tokens'}),
               'refusal': ('incomplete', {'reason': 'content_filter'})}


class TranslationError(ValueError):
    """The Codex request uses a feature this bridge does not translate."""


class UpstreamStreamError(RuntimeError):
    def __init__(self, error: dict):
        self.error = error or {}
        super().__init__(json.dumps(self.error, ensure_ascii=False)[:1000])


# ---------------------------------------------------------------- request side

def flatten_tools(tools, namespace=None):
    for tool in tools or []:
        if tool.get('type') == 'namespace':
            yield from flatten_tools(tool.get('tools', []), tool['name'])
            continue
        value = dict(tool)
        if namespace:
            value['_namespace'] = namespace
            value['_original_name'] = tool['name']
            value['name'] = namespace + '__' + tool['name']
        yield value


def encode_reasoning(blocks: list[dict]) -> str:
    raw = json.dumps(blocks, ensure_ascii=False, separators=(',', ':')).encode()
    return REASONING_PREFIX + base64.b64encode(raw).decode()


def decode_reasoning(value) -> list[dict] | None:
    if not isinstance(value, str) or not value.startswith(REASONING_PREFIX):
        return None
    blocks = json.loads(base64.b64decode(value[len(REASONING_PREFIX):]))
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        raise TranslationError('Malformed bridged reasoning content')
    return blocks


def image_block(url) -> dict:
    if isinstance(url, dict):
        url = url.get('url')
    if not isinstance(url, str) or not url:
        raise TranslationError('Image part without a URL')
    if url.startswith('data:'):
        header, _, data = url.partition(',')
        media = header[5:].split(';', 1)[0]
        if ';base64' not in header or not data:
            raise TranslationError('Only base64 data URLs are supported for images')
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': data}}
    return {'type': 'image', 'source': {'type': 'url', 'url': url}}


def content_blocks(value) -> list[dict]:
    if isinstance(value, str):
        return [{'type': 'text', 'text': value}] if value else []
    blocks = []
    for part in value or []:
        kind = part.get('type')
        if kind in ('input_text', 'output_text', 'text'):
            if part.get('text'):
                blocks.append({'type': 'text', 'text': part['text']})
        elif kind in ('input_image', 'image_url'):
            blocks.append(image_block(part.get('image_url')))
        else:
            raise TranslationError('Unsupported content part: ' + str(kind))
    return blocks


class Conversation:
    """Messages list that merges consecutive same-role turns."""

    def __init__(self):
        self.messages: list[dict] = []

    def add(self, role: str, blocks: list[dict]):
        if not blocks:
            return
        if self.messages and self.messages[-1]['role'] == role:
            self.messages[-1]['content'].extend(blocks)
        else:
            self.messages.append({'role': role, 'content': list(blocks)})


def tool_output_blocks(output) -> list[dict] | str:
    if isinstance(output, list):
        blocks = content_blocks(output)
        return blocks or [{'type': 'text', 'text': '(no output)'}]
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return text if text else '(no output)'


def translate_tools(tools) -> list[dict]:
    result = []
    for tool in flatten_tools(tools):
        kind = tool.get('type')
        if kind == 'function':
            schema = tool.get('parameters') or {'type': 'object', 'properties': {}}
            entry = {'name': tool['name'], 'description': tool.get('description', ''),
                     'input_schema': schema}
        elif kind == 'custom':
            description = (tool.get('description', '') +
                           '\nPass the complete original freeform tool input in the input argument.')
            fmt = tool.get('format') or {}
            if fmt.get('type') == 'grammar':
                description += '\nOriginal input grammar:\n' + fmt.get('definition', '')
            entry = {'name': tool['name'], 'description': description,
                     'input_schema': {'type': 'object', 'properties': {'input': {'type': 'string'}},
                                      'required': ['input'], 'additionalProperties': False}}
        else:
            raise TranslationError('Unsupported tool type: ' + str(kind))
        result.append(entry)
    return result


def messages_request(req: dict, *, upstream_model: str, default_max_tokens: int,
                     cache_ttl: str | None, thinking_display: str) -> tuple[dict, dict]:
    """Return (Messages params, translation notes)."""
    if req.get('previous_response_id'):
        raise TranslationError('Stateless full input is required; previous_response_id is unsupported')
    notes = {'dropped_foreign_reasoning': 0, 'late_developer_messages': 0, 'restored_thinking_blocks': 0}
    system: list[dict] = []
    if req.get('instructions'):
        system.extend(content_blocks(req['instructions']))
    conversation = Conversation()
    items = req.get('input', [])
    if isinstance(items, str):
        items = [{'type': 'message', 'role': 'user', 'content': items}]
    for item in items:
        kind = item.get('type', 'message')
        if kind == 'message':
            role = item.get('role', 'user')
            blocks = content_blocks(item.get('content', []))
            if role in ('developer', 'system'):
                if not conversation.messages:
                    system.extend(b for b in blocks if b['type'] == 'text')
                    continue
                notes['late_developer_messages'] += 1
                blocks = [{'type': 'text', 'text': '[developer instruction]\n' + b['text']}
                          if b['type'] == 'text' else b for b in blocks]
                role = 'user'
            conversation.add('assistant' if role == 'assistant' else 'user', blocks)
        elif kind == 'reasoning':
            blocks = decode_reasoning(item.get('encrypted_content'))
            if blocks is None:
                notes['dropped_foreign_reasoning'] += 1
                continue
            notes['restored_thinking_blocks'] += len(blocks)
            conversation.add('assistant', blocks)
        elif kind in ('function_call', 'custom_tool_call'):
            name = item['name']
            if item.get('namespace'):
                name = item['namespace'] + '__' + name
            if kind == 'custom_tool_call':
                arguments = {'input': item.get('input', '')}
            else:
                raw = item.get('arguments') or '{}'
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise TranslationError('Replayed function arguments are not JSON') from error
                if not isinstance(arguments, dict):
                    raise TranslationError('Replayed function arguments must be a JSON object')
            conversation.add('assistant', [{'type': 'tool_use', 'id': item['call_id'],
                                            'name': name, 'input': arguments}])
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            conversation.add('user', [{'type': 'tool_result', 'tool_use_id': item['call_id'],
                                       'content': tool_output_blocks(item.get('output', ''))}])
        else:
            raise TranslationError('Unsupported input item: ' + str(kind))
    if not conversation.messages or conversation.messages[0]['role'] != 'user':
        conversation.messages.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': '(start)'}]})
    effort = (req.get('reasoning') or {}).get('effort')
    if effort is not None and effort not in EFFORTS:
        raise TranslationError('Unsupported reasoning effort: ' + str(effort))
    max_tokens = req.get('max_output_tokens', default_max_tokens)
    if type(max_tokens) is not int or max_tokens < 1:
        raise TranslationError('max_output_tokens must be a positive integer')
    params = {'model': upstream_model, 'max_tokens': max_tokens,
              'messages': conversation.messages,
              'thinking': {'type': 'adaptive', 'display': thinking_display}}
    if system:
        params['system'] = system
    if effort is not None:
        params['output_config'] = {'effort': EFFORTS[effort]}
    tools = translate_tools(req.get('tools'))
    if tools:
        params['tools'] = tools
        choice = {'type': 'auto'}
        if req.get('parallel_tool_calls') is False:
            choice['disable_parallel_tool_use'] = True
        if req.get('tool_choice') not in (None, 'auto'):
            raise TranslationError('Only automatic tool choice is supported on this model')
        params['tool_choice'] = choice
    cache = {'type': 'ephemeral'}
    if cache_ttl:
        cache['ttl'] = cache_ttl
    params['cache_control'] = cache
    return params, notes


# --------------------------------------------------------------- response side

def sse_events(lines):
    event, data = None, []
    for line in lines:
        if line == '':
            if data:
                yield event, '\n'.join(data)
            event, data = None, []
        elif line.startswith('event:'):
            event = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
    if data:
        yield event, '\n'.join(data)


def responses_usage(usage: dict) -> dict:
    fresh = usage.get('input_tokens') or 0
    read = usage.get('cache_read_input_tokens') or 0
    write = usage.get('cache_creation_input_tokens') or 0
    output = usage.get('output_tokens') or 0
    return {'input_tokens': fresh + read + write, 'output_tokens': output,
            'total_tokens': fresh + read + write + output,
            'input_tokens_details': {'cached_tokens': read, 'cache_write_tokens': write},
            'output_tokens_details': {'reasoning_tokens': 0}}


class ResponseBuilder:
    """Turns Anthropic stream events into Responses stream events."""

    def __init__(self, rid: str, model: str, created: int, tools: list[dict], emit):
        self.rid, self.model, self.created, self.emit = rid, model, created, emit
        self.tools = {t['name']: t for t in flatten_tools(tools)}
        self.outputs: list[dict] = []
        self.blocks: dict[int, dict] = {}
        self.usage: dict = {}
        self.stop_reason = None
        self.message_id = None
        self.served_model = None
        self.finished = False

    def response(self, status: str, error=None, details=None) -> dict:
        return {'id': self.rid, 'object': 'response', 'created_at': self.created, 'status': status,
                'model': self.model, 'output': self.outputs,
                'usage': responses_usage(self.usage) if self.usage else None,
                'error': error, 'incomplete_details': details}

    def start(self):
        self.emit('response.created', response=self.response('in_progress'))
        self.emit('response.in_progress', response=self.response('in_progress'))

    def handle(self, event: str | None, data: dict):
        kind = data.get('type', event)
        if kind == 'message_start':
            message = data.get('message', {})
            self.message_id, self.served_model = message.get('id'), message.get('model')
            self.usage.update(message.get('usage') or {})
        elif kind == 'content_block_start':
            self._block_start(data['index'], data['content_block'])
        elif kind == 'content_block_delta':
            self._block_delta(data['index'], data['delta'])
        elif kind == 'content_block_stop':
            self._block_stop(data['index'])
        elif kind == 'message_delta':
            self.stop_reason = (data.get('delta') or {}).get('stop_reason') or self.stop_reason
            for key, value in (data.get('usage') or {}).items():
                if value is not None:
                    self.usage[key] = value
        elif kind == 'message_stop':
            self.finished = True
        elif kind == 'error':
            raise UpstreamStreamError(data.get('error') or data)
        # ping and unknown future events are preserved in the raw log only.

    def _block_start(self, index: int, block: dict):
        kind = block.get('type')
        state = {'type': kind, 'block': dict(block)}
        if kind == 'text':
            item = {'id': 'msg_' + uuid.uuid4().hex, 'type': 'message', 'status': 'in_progress',
                    'role': 'assistant', 'content': []}
            state['item'], state['index'] = item, len(self.outputs)
            state['block']['text'] = block.get('text', '')
            self.outputs.append(item)
            self.emit('response.output_item.added', output_index=state['index'], item=dict(item))
            part = {'type': 'output_text', 'text': '', 'annotations': []}
            item['content'].append(part)
            self.emit('response.content_part.added', item_id=item['id'], output_index=state['index'],
                      content_index=0, part=dict(part))
            if state['block']['text']:
                self._text(state, state['block']['text'])
        elif kind == 'thinking':
            state['block'].setdefault('thinking', '')
            state['block'].setdefault('signature', '')
        elif kind == 'redacted_thinking':
            pass
        elif kind == 'tool_use':
            name = block['name']
            original = self.tools.get(name)
            custom = bool(original and original.get('type') == 'custom')
            item = {'id': 'fc_' + uuid.uuid4().hex,
                    'type': 'custom_tool_call' if custom else 'function_call',
                    'status': 'in_progress', 'call_id': block['id'],
                    'name': original.get('_original_name', name) if original else name,
                    ('input' if custom else 'arguments'): ''}
            if original and original.get('_namespace'):
                item['namespace'] = original['_namespace']
            state.update(item=item, index=len(self.outputs), custom=custom, json='')
            self.outputs.append(item)
            self.emit('response.output_item.added', output_index=state['index'], item=dict(item))
        else:
            raise UpstreamStreamError({'type': 'unsupported_content_block', 'message': str(kind)})
        self.blocks[index] = state

    def _text(self, state, delta):
        item = state['item']
        item['content'][0]['text'] += delta
        self.emit('response.output_text.delta', item_id=item['id'], output_index=state['index'],
                  content_index=0, delta=delta)

    def _block_delta(self, index: int, delta: dict):
        state = self.blocks[index]
        kind = delta.get('type')
        if kind == 'text_delta':
            state['block']['text'] = state['block'].get('text', '') + delta['text']
            self._text(state, delta['text'])
        elif kind == 'thinking_delta':
            state['block']['thinking'] += delta.get('thinking', '')
        elif kind == 'signature_delta':
            state['block']['signature'] += delta.get('signature', '')
        elif kind == 'input_json_delta':
            state['json'] += delta.get('partial_json', '')
            if not state['custom'] and delta.get('partial_json'):
                state['item']['arguments'] += delta['partial_json']
                self.emit('response.function_call_arguments.delta', item_id=state['item']['id'],
                          output_index=state['index'], delta=delta['partial_json'])
        # citations and other future deltas remain in the raw log.

    def _block_stop(self, index: int):
        state = self.blocks[index]
        kind = state['type']
        if kind == 'text':
            item = state['item']
            item['status'] = 'completed'
            text = item['content'][0]['text']
            self.emit('response.output_text.done', item_id=item['id'], output_index=state['index'],
                      content_index=0, text=text)
            self.emit('response.content_part.done', item_id=item['id'], output_index=state['index'],
                      content_index=0, part=item['content'][0])
            self.emit('response.output_item.done', output_index=state['index'], item=item)
        elif kind in ('thinking', 'redacted_thinking'):
            block = state['block']
            summary = ([{'type': 'summary_text', 'text': block['thinking']}]
                       if kind == 'thinking' and block.get('thinking') else [])
            item = {'id': 'rs_' + uuid.uuid4().hex, 'type': 'reasoning', 'summary': summary,
                    'encrypted_content': encode_reasoning([block])}
            index_out = len(self.outputs)
            self.outputs.append(item)
            self.emit('response.output_item.added', output_index=index_out,
                      item={**item, 'summary': []})
            self.emit('response.output_item.done', output_index=index_out, item=item)
        elif kind == 'tool_use':
            item = state['item']
            raw = state['json'] or '{}'
            if state['custom']:
                try:
                    value = json.loads(raw).get('input')
                except (json.JSONDecodeError, AttributeError):
                    value = None
                if not isinstance(value, str):
                    raise UpstreamStreamError({'type': 'invalid_custom_tool_input',
                                               'message': 'Wrapped custom tool did not return a string input'})
                item['input'] = value
                self.emit('response.custom_tool_call_input.delta', item_id=item['id'],
                          output_index=state['index'], delta=value)
                self.emit('response.custom_tool_call_input.done', item_id=item['id'],
                          output_index=state['index'], input=value)
            else:
                if not state['json']:
                    item['arguments'] = '{}'
                self.emit('response.function_call_arguments.done', item_id=item['id'],
                          output_index=state['index'], arguments=item['arguments'])
            item['status'] = 'completed'
            state['closed'] = True
            self.emit('response.output_item.done', output_index=state['index'], item=item)

    def finish(self) -> tuple[str, dict]:
        if not self.finished:
            raise UpstreamStreamError({'type': 'stream_disconnected_before_terminal',
                                       'message': 'Upstream stream ended without message_stop'})
        if self.stop_reason not in STOP_STATUS:
            raise UpstreamStreamError({'type': 'unsupported_stop_reason', 'message': str(self.stop_reason)})
        status, details = STOP_STATUS[self.stop_reason]
        if status != 'completed':
            for item in self.outputs:
                if item.get('status') == 'in_progress':
                    item['status'] = 'incomplete'
        final = self.response(status, details=details)
        self.emit('response.' + status, response=final)
        return status, final


# ------------------------------------------------------------------- HTTP side

class Bridge(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def json_reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.local_key)

    def do_GET(self):
        if self.path == '/health':
            return self.json_reply(200, {'ok': True, 'adapter': ADAPTER})
        if not self.authorized():
            return self.json_reply(401, {'error': {'message': 'Local proxy authentication required'}})
        if self.path in ('/v1/models', '/models'):
            return self.json_reply(200, {'object': 'list', 'data': [
                {'id': slug, 'object': 'model', 'owned_by': 'anthropic-bridge'} for slug in self.server.model_map]})
        self.json_reply(404, {'error': {'message': 'Unknown path'}})

    def save(self, rid, suffix, value, raw=False):
        data = value if raw else json.dumps(value, ensure_ascii=False).encode()
        path = self.server.log_dir / f'{rid}.{suffix}'
        path.write_bytes(data)
        path.chmod(0o600)

    def do_POST(self):
        if not self.authorized():
            return self.json_reply(401, {'error': {'message': 'Local proxy authentication required'}})
        if self.path not in ('/v1/responses', '/responses'):
            return self.json_reply(404, {'error': {'message': 'Unknown path'}})
        import anthropic
        rid = 'resp_' + uuid.uuid4().hex
        started = time.time()
        record = {'id': rid, 'adapter': ADAPTER, 'started': started}
        raw = bytearray()
        builder = None
        stream_started = False
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size > self.server.max_request_bytes:
                raise TranslationError('Request exceeds local size limit')
            req = json.loads(self.rfile.read(size))
            self.save(rid, 'input.json', req)
            streaming = bool(req.get('stream', False))
            upstream_model = self.server.model_map.get(req.get('model'))
            if upstream_model is None:
                raise TranslationError('Model is not enabled on this bridge: ' + str(req.get('model')))
            params, notes = messages_request(req, upstream_model=upstream_model,
                                             default_max_tokens=self.server.default_max_tokens,
                                             cache_ttl=self.server.cache_ttl,
                                             thinking_display=self.server.thinking_display)
            record.update(model=req.get('model'), upstream_model=upstream_model,
                          effort=(params.get('output_config') or {}).get('effort'),
                          thread_id=req.get('prompt_cache_key'), input_items=len(req.get('input', [])),
                          messages=len(params['messages']), translation=notes,
                          ignored_fields=sorted(set(req) - {
                              'model', 'instructions', 'input', 'tools', 'tool_choice', 'parallel_tool_calls',
                              'reasoning', 'stream', 'max_output_tokens', 'store', 'include',
                              'prompt_cache_key'}))
            self.save(rid, 'native.json', params)
            emit_lock = threading.Lock()
            sequence = 0

            def emit(kind, **fields):
                nonlocal sequence
                if not streaming:
                    return
                with emit_lock:
                    obj = {'type': kind, 'sequence_number': sequence, **fields}
                    sequence += 1
                    self.wfile.write(('event: ' + kind + '\ndata: ' +
                                      json.dumps(obj, ensure_ascii=False) + '\n\n').encode())
                    self.wfile.flush()

            client = self.server.client_factory()
            try:
                with client.messages.with_streaming_response.create(stream=True, **params) as upstream:
                    record['http_status'] = upstream.status_code
                    record['upstream_request_id'] = upstream.headers.get('request-id')
                    if streaming:
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Cache-Control', 'no-cache')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        stream_started = True
                    builder = ResponseBuilder(rid, req['model'], int(started), req.get('tools'), emit)
                    builder.start()

                    def lines():
                        for line in upstream.iter_lines():
                            raw.extend(line.encode() + b'\n')
                            yield line
                    for event, data in sse_events(lines()):
                        try:
                            value = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        builder.handle(event, value)
            except anthropic.APIStatusError as error:
                record['http_status'] = error.status_code
                record['upstream_request_id'] = error.response.headers.get('request-id')
                body = error.response.text if error.response is not None else str(error)
                self.save(rid, 'error.txt', body.encode(), raw=True)
                record['upstream_error_preview'] = body[:500]
                return self.json_reply(error.status_code, {'error': {
                    'message': 'Upstream rejected request: ' + body[:800]}})
            status, final = builder.finish()
            if not streaming:
                self.json_reply(200, final)
            record.update(response_status=status, stop_reason=builder.stop_reason,
                          served_model=builder.served_model, message_id=builder.message_id,
                          native_usage=builder.usage, usage=final['usage'],
                          reasoning_tokens='unavailable',
                          output_types=[item['type'] for item in builder.outputs])
            self.save(rid, 'response.json', final)
        except (BrokenPipeError, ConnectionResetError):
            record['client_disconnected'] = True
        except Exception as error:  # every failure is recorded and surfaced to Codex
            record['error_type'] = type(error).__name__
            record['error_message'] = str(error)[:500]
            if builder is not None and builder.usage:
                record['native_usage'] = builder.usage
                record['usage'] = responses_usage(builder.usage)
            code = ((error.error.get('type') if isinstance(error, UpstreamStreamError) else None)
                    or ('invalid_request' if isinstance(error, TranslationError) else 'upstream_error'))
            try:
                if stream_started and builder is not None:
                    builder.emit('response.failed', response=builder.response(
                        'failed', error={'code': code, 'message': str(error)[:1000]}))
                else:
                    self.json_reply(400 if isinstance(error, TranslationError) else 502,
                                    {'error': {'message': str(error)[:1000], 'code': code}})
            except Exception:
                pass
        finally:
            if raw:
                self.save(rid, 'response.raw', bytes(raw), raw=True)
            record['elapsed'] = time.time() - started
            with self.server.lock:
                with (self.server.log_dir / 'requests.jsonl').open('a') as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            print(json.dumps({k: record.get(k) for k in (
                'id', 'model', 'http_status', 'response_status', 'stop_reason', 'usage',
                'error_type', 'error_message', 'elapsed')}, ensure_ascii=False), flush=True)
            self.close_connection = True


def make_server(*, port: int, log_dir: Path, model_map: dict[str, str], client_factory,
                default_max_tokens: int = 128000, cache_ttl: str | None = None,
                thinking_display: str = 'summarized', max_request_bytes: int = 64 * 1024 * 1024,
                local_key: str | None = None) -> ThreadingHTTPServer:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    server = ThreadingHTTPServer(('127.0.0.1', port), Bridge)
    server.log_dir, server.model_map, server.client_factory = log_dir, model_map, client_factory
    server.default_max_tokens, server.cache_ttl = default_max_tokens, cache_ttl
    server.thinking_display, server.max_request_bytes = thinking_display, max_request_bytes
    server.lock = threading.Lock()
    key_file = log_dir / 'local.key'
    existing = key_file.read_text().strip() if key_file.exists() else ''
    server.local_key = local_key or existing or secrets.token_urlsafe(32)
    key_file.write_text(server.local_key)
    key_file.chmod(0o600)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=9590)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--model', action='append', required=True,
                        help='Codex model slug, optionally SLUG=UPSTREAM_MODEL_ID (repeatable)')
    parser.add_argument('--default-max-tokens', type=int, default=128000)
    parser.add_argument('--cache-ttl', choices=('5m', '1h'), default=None)
    parser.add_argument('--thinking-display', choices=('summarized', 'omitted'), default='summarized')
    parser.add_argument('--upstream-timeout', type=float, default=3600)
    args = parser.parse_args()
    model_map = {}
    for value in args.model:
        slug, _, upstream = value.partition('=')
        model_map[slug] = upstream or slug
    import anthropic

    def client_factory():
        # Retries stay with Codex so every attempt is visible in its own log.
        return anthropic.Anthropic(max_retries=0, timeout=args.upstream_timeout)
    server = make_server(port=args.port, log_dir=args.log_dir, model_map=model_map,
                         client_factory=client_factory, default_max_tokens=args.default_max_tokens,
                         cache_ttl=args.cache_ttl, thinking_display=args.thinking_display)
    print('READY http://127.0.0.1:%d/v1' % server.server_port, flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
