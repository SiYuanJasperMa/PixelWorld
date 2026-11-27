import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pixelworld import anthropic_responses_proxy as bridge

anthropic = pytest.importorskip('anthropic')


def sse(events):
    out = []
    for event in events:
        out.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return ''.join(out).encode()


def scripted_turn(blocks, stop_reason='end_turn', usage=None):
    usage = usage or {'input_tokens': 10, 'cache_read_input_tokens': 900,
                      'cache_creation_input_tokens': 90, 'output_tokens': 1}
    events = [{'type': 'message_start', 'message': {
        'id': 'msg_up', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
        'content': [], 'stop_reason': None, 'usage': usage}}, {'type': 'ping'}]
    for index, block in enumerate(blocks):
        kind = block['type']
        if kind == 'thinking':
            events += [{'type': 'content_block_start', 'index': index,
                        'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
                       {'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'thinking_delta', 'thinking': block['thinking']}},
                       {'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'signature_delta', 'signature': block['signature']}}]
        elif kind == 'text':
            events += [{'type': 'content_block_start', 'index': index,
                        'content_block': {'type': 'text', 'text': ''}},
                       {'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'text_delta', 'text': block['text']}}]
        elif kind == 'tool_use':
            text = json.dumps(block['input'])
            events += [{'type': 'content_block_start', 'index': index,
                        'content_block': {'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': {}}},
                       {'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'input_json_delta', 'partial_json': text[:5]}},
                       {'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'input_json_delta', 'partial_json': text[5:]}}]
        events.append({'type': 'content_block_stop', 'index': index})
    events += [{'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None},
                'usage': {'output_tokens': 77}}, {'type': 'message_stop'}]
    return sse(events)


class FakeAnthropic:
    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'path': self.path, 'body': body, 'headers': dict(self.headers)})
                status, payload = owner.turns.pop(0)
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
                self.send_header('request-id', 'req_fake')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self

    def __exit__(self, *args):
        self.server.shutdown()


@pytest.fixture
def run_bridge(tmp_path):
    servers = []

    def start(fake):
        server = bridge.make_server(
            port=0, log_dir=tmp_path / 'logs', model_map={'claude-opus-5-5': 'claude-opus-5-5'},
            client_factory=lambda: anthropic.Anthropic(base_url=fake.url, api_key='test-key', max_retries=0),
            local_key='local')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f'http://127.0.0.1:{server.server_port}'
    yield start
    for server in servers:
        server.shutdown()


def post(url, body):
    request = urllib.request.Request(url + '/v1/responses', data=json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer local', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def stream_events(text):
    return [json.loads(data) for _, data in bridge.sse_events(text.split('\n'))]


TOOLS = [{'type': 'function', 'name': 'shell_command', 'description': 'run',
          'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}},
         {'type': 'custom', 'name': 'apply_patch', 'description': 'patch',
          'format': {'type': 'grammar', 'syntax': 'lark', 'definition': 'start: "x"'}}]


def test_translation_restores_thinking_and_groups_tool_turns():
    thinking = {'type': 'thinking', 'thinking': 'plan', 'signature': 'sig'}
    req = {'model': 'claude-opus-5-5', 'instructions': 'base rules', 'reasoning': {'effort': 'high'},
           'parallel_tool_calls': False, 'tools': TOOLS, 'input': [
               {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'perm'}]},
               {'type': 'message', 'role': 'user', 'content': [
                   {'type': 'input_text', 'text': 'go'},
                   {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA'}]},
               {'type': 'reasoning', 'summary': [], 'encrypted_content': bridge.encode_reasoning([thinking])},
               {'type': 'function_call', 'name': 'shell_command', 'call_id': 'c1', 'arguments': '{"command":"ls"}'},
               {'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'c2', 'input': '*** Begin'},
               {'type': 'function_call_output', 'call_id': 'c1', 'output': 'a.py'},
               {'type': 'custom_tool_call_output', 'call_id': 'c2', 'output': [
                   {'type': 'input_image', 'image_url': 'data:image/png;base64,BBBB'}]},
               {'type': 'reasoning', 'summary': [], 'encrypted_content': 'gAAAA-openai-blob'},
               {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'done'}]}]}
    params, notes = bridge.messages_request(req, upstream_model='claude-opus-5-5', default_max_tokens=128000,
                                            cache_ttl=None, thinking_display='summarized')
    assert [b['text'] for b in params['system']] == ['base rules', 'perm']
    assert params['output_config'] == {'effort': 'high'}
    assert params['thinking'] == {'type': 'adaptive', 'display': 'summarized'}
    assert params['tool_choice'] == {'type': 'auto', 'disable_parallel_tool_use': True}
    assert params['cache_control'] == {'type': 'ephemeral'}
    assert 'fallbacks' not in params
    roles = [m['role'] for m in params['messages']]
    assert roles == ['user', 'assistant', 'user', 'assistant']
    assistant = params['messages'][1]['content']
    assert assistant[0] == thinking
    assert assistant[1] == {'type': 'tool_use', 'id': 'c1', 'name': 'shell_command', 'input': {'command': 'ls'}}
    assert assistant[2]['input'] == {'input': '*** Begin'}
    results = params['messages'][2]['content']
    assert [r['tool_use_id'] for r in results] == ['c1', 'c2']
    assert results[1]['content'][0]['source']['data'] == 'BBBB'
    assert params['messages'][0]['content'][1]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': 'AAAA'}
    assert notes['dropped_foreign_reasoning'] == 1 and notes['restored_thinking_blocks'] == 1
    custom = [t for t in params['tools'] if t['name'] == 'apply_patch'][0]
    assert custom['input_schema']['required'] == ['input'] and 'start: "x"' in custom['description']


def test_translation_rejects_stateful_and_forced_tool_requests():
    with pytest.raises(bridge.TranslationError):
        bridge.messages_request({'model': 'm', 'previous_response_id': 'r', 'input': []}, upstream_model='m',
                                default_max_tokens=1, cache_ttl=None, thinking_display='omitted')
    with pytest.raises(bridge.TranslationError):
        bridge.messages_request({'model': 'm', 'tools': TOOLS, 'tool_choice': 'required', 'input': 'x'},
                                upstream_model='m', default_max_tokens=1, cache_ttl=None, thinking_display='omitted')


def test_stream_round_trip_maps_items_usage_and_reasoning(run_bridge, tmp_path):
    turn = scripted_turn([{'type': 'thinking', 'thinking': 'think', 'signature': 'SIG'},
                          {'type': 'text', 'text': 'Running.'},
                          {'type': 'tool_use', 'id': 'toolu_1', 'name': 'shell_command', 'input': {'command': 'ls'}},
                          {'type': 'tool_use', 'id': 'toolu_2', 'name': 'apply_patch', 'input': {'input': 'PATCH'}}],
                         stop_reason='tool_use')
    with FakeAnthropic([(200, turn)]) as fake:
        url = run_bridge(fake)
        status, text = post(url, {'model': 'claude-opus-5-5', 'stream': True, 'tools': TOOLS,
                                  'reasoning': {'effort': 'high'}, 'input': 'hello'})
    assert status == 200
    events = stream_events(text)
    final = events[-1]
    assert final['type'] == 'response.completed'
    out = final['response']['output']
    assert [o['type'] for o in out] == ['reasoning', 'message', 'function_call', 'custom_tool_call']
    assert bridge.decode_reasoning(out[0]['encrypted_content']) == [
        {'type': 'thinking', 'thinking': 'think', 'signature': 'SIG'}]
    assert out[1]['content'][0]['text'] == 'Running.'
    assert json.loads(out[2]['arguments']) == {'command': 'ls'} and out[2]['call_id'] == 'toolu_1'
    assert out[3]['input'] == 'PATCH'
    assert final['response']['usage'] == {'input_tokens': 1000, 'output_tokens': 77, 'total_tokens': 1077,
                                          'input_tokens_details': {'cached_tokens': 900, 'cache_write_tokens': 90},
                                          'output_tokens_details': {'reasoning_tokens': 0}}
    sent = fake.requests[0]
    assert sent['path'] == '/v1/messages' and sent['body']['stream'] is True
    assert sent['body']['output_config'] == {'effort': 'high'}
    log = [json.loads(line) for line in (tmp_path / 'logs' / 'requests.jsonl').read_text().splitlines()]
    assert log[0]['upstream_request_id'] == 'req_fake' and log[0]['reasoning_tokens'] == 'unavailable'
    assert log[0]['native_usage']['cache_creation_input_tokens'] == 90
    raw = list((tmp_path / 'logs').glob('*.response.raw'))[0].read_bytes()
    assert b'signature_delta' in raw and b'test-key' not in raw


def test_max_tokens_and_refusal_are_incomplete(run_bridge):
    for stop, reason in (('max_tokens', 'max_output_tokens'), ('refusal', 'content_filter')):
        with FakeAnthropic([(200, scripted_turn([{'type': 'text', 'text': 'x'}], stop_reason=stop))]) as fake:
            url = run_bridge(fake)
            _, text = post(url, {'model': 'claude-opus-5-5', 'stream': True, 'input': 'hi'})
        final = stream_events(text)[-1]
        assert final['type'] == 'response.incomplete'
        assert final['response']['incomplete_details'] == {'reason': reason}


def test_upstream_rate_limit_status_is_returned_for_codex_retry(run_bridge):
    body = json.dumps({'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'slow down'}}).encode()
    with FakeAnthropic([(429, body)]) as fake:
        url = run_bridge(fake)
        status, text = post(url, {'model': 'claude-opus-5-5', 'stream': True, 'input': 'hi'})
    assert status == 429 and 'slow down' in text


def test_mid_stream_error_fails_the_response(run_bridge):
    events = sse([{'type': 'message_start', 'message': {'id': 'm', 'model': 'claude-opus-5-5',
                                                        'usage': {'input_tokens': 3, 'output_tokens': 0}}},
                  {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'busy'}}])
    with FakeAnthropic([(200, events)]) as fake:
        url = run_bridge(fake)
        _, text = post(url, {'model': 'claude-opus-5-5', 'stream': True, 'input': 'hi'})
    final = stream_events(text)[-1]
    assert final['type'] == 'response.failed'
    assert final['response']['error']['code'] == 'overloaded_error'


def test_unknown_model_and_local_auth_are_rejected(run_bridge):
    with FakeAnthropic([]) as fake:
        url = run_bridge(fake)
        status, _ = post(url, {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'hi'})
        assert status == 400
        request = urllib.request.Request(url + '/v1/responses', data=b'{}', headers={'Authorization': 'Bearer no'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 401
    assert fake.requests == []
