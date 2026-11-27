import base64
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pixelworld import antigravity_harness as agh
from pixelworld import antigravity_sandbox as ags
from pixelworld.cloudcode_relay import cloudcode_relay, output_tokens, parse_stream
from pixelworld.config import load_config
from pixelworld.egress_allowlist_proxy import egress_proxy
from pixelworld.token_budget import ResponseUsageBudget


def sse(chunks):
    return ''.join(f'data: {json.dumps(c)}\r\n\r\n' for c in chunks).encode()


def gemini_stream(candidates=7, thoughts=5, model='gemini-4-neon', rid='resp-1'):
    usage = {'promptTokenCount': 100, 'candidatesTokenCount': candidates, 'totalTokenCount': 100 + candidates + thoughts,
             'thoughtsTokenCount': thoughts, 'cachedContentTokenCount': 40}
    part = {'response': {'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'hi'}]}}],
                         'usageMetadata': dict(usage, candidatesTokenCount=1), 'modelVersion': model, 'responseId': rid},
            'traceId': 't1'}
    last = {'response': {'candidates': [{'content': {'role': 'model', 'parts': [{'text': '!'}]}, 'finishReason': 'STOP'}],
                         'usageMetadata': usage, 'modelVersion': model, 'responseId': rid}, 'traceId': 't1'}
    return sse([part, last])


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
                self.send_header('Content-Type', 'text/event-stream' if payload.startswith(b'data:') else 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{self.server.server_port}'

    def __exit__(self, *args):
        self.server.shutdown()


def post(base, path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={
        'Authorization': 'Bearer ya29.SECRET-ACCESS', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def agent_call(model='gemini-4-neon-high', kind='custom_agent', text='hello'):
    return {'project': 'p', 'model': model, 'requestType': kind, 'userAgent': 'antigravity',
            'request': {'contents': [{'role': 'user', 'parts': [{'text': text}]}]}}


STREAM = '/v1internal:streamGenerateContent?alt=sse'


def test_host_authentication_replaces_client_headers_without_recording_secret(tmp_path):
    fake = Upstream([gemini_stream()])
    host_secret = 'host-private-test-access-token'
    with fake as upstream:
        with cloudcode_relay(wire=tmp_path/'wire', model='gemini-4-neon-high',
                             budget=ResponseUsageBudget(100), upstream=upstream,
                             host_access_token=lambda: host_secret) as relay:
            assert post(relay, STREAM, agent_call())[0] == 200
    assert fake.seen[0][1]['Authorization'] == 'Bearer ' + host_secret
    for p in (tmp_path/'wire').rglob('*'):
        if p.is_file():
            assert host_secret.encode() not in p.read_bytes()


def test_host_authentication_unavailable_never_samples(tmp_path):
    fake = Upstream([])
    def unavailable():
        raise RuntimeError('operator login unavailable')
    with fake as upstream:
        with cloudcode_relay(wire=tmp_path/'wire', model='gemini-4-neon-high',
                             budget=ResponseUsageBudget(100), upstream=upstream,
                             host_access_token=unavailable) as relay:
            assert post(relay, STREAM, agent_call())[0] == 502
    assert fake.seen == []


def test_parse_stream_takes_final_usage_and_counts_thoughts_as_output():
    parsed = parse_stream(gemini_stream(), 'text/event-stream')
    assert parsed['model_version'] == 'gemini-4-neon' and parsed['finish_reason'] == 'STOP'
    assert output_tokens(parsed['usage']) == 12
    assert output_tokens({'candidatesTokenCount': 3}) == 3
    assert output_tokens(None) is None


def test_relay_records_model_calls_pins_model_and_budget(tmp_path):
    budget = ResponseUsageBudget(20)
    with Upstream([gemini_stream(rid='a'), gemini_stream(rid='b', candidates=10)]) as upstream:
        with cloudcode_relay(wire=tmp_path/'wire', model='gemini-4-neon-high', budget=budget, upstream=upstream,
                             agent_request_types=('custom_agent', 'agent')) as relay:
            assert post(relay, STREAM, agent_call())[0] == 200
            # Auxiliary model (title / safety rater) and a wrong request type are refused locally.
            assert post(relay, STREAM, agent_call(model='gemini-3.5-flash-lite', kind='checkpoint'))[0] == 400
            assert post(relay, STREAM, agent_call(kind='checkpoint'))[0] == 400
            assert post(relay, STREAM, agent_call())[0] == 200
            # 12 + 15 >= 20: the budget is crossed, the next call never leaves the machine.
            assert not budget.can_dispatch
            status, body = post(relay, STREAM, agent_call())
            assert status == 429 and b'budget' in body
            assert post(relay, '/v1internal:searchWeb', {})[0] == 403
    receipts = [json.loads(p.read_text()) for p in sorted((tmp_path/'wire').glob('*/receipt.json'))]
    assert [r.get('refused') is None for r in receipts] == [True, False, False, True, False, False]
    assert receipts[0]['model_version'] == 'gemini-4-neon' and receipts[0]['output_tokens'] == 12
    assert budget.report()['reported_output_tokens'] == 27
    for folder in (tmp_path/'wire').iterdir():
        for f in folder.iterdir():
            assert b'SECRET' not in f.read_bytes()


def test_relay_does_not_record_session_method_bodies(tmp_path):
    with Upstream([json.dumps({'email': 'operator@example.com'}).encode()]) as upstream:
        with cloudcode_relay(wire=tmp_path/'wire', model='m', budget=ResponseUsageBudget(10), upstream=upstream) as relay:
            status, body = post(relay, '/v1internal:loadCodeAssist', {'metadata': {}})
            assert status == 200 and b'operator@example.com' in body
    folder = next((tmp_path/'wire').iterdir())
    assert sorted(f.name for f in folder.iterdir()) == ['receipt.json']
    receipt = json.loads((folder/'receipt.json').read_text())
    assert receipt['response_bytes'] > 0 and 'operator' not in json.dumps(receipt)


def test_relay_refuses_requests_carrying_credentials(tmp_path):
    fake = Upstream([])
    with fake as upstream:
        with cloudcode_relay(wire=tmp_path/'wire', model='gemini-4-neon-high', budget=ResponseUsageBudget(10),
                             upstream=upstream, secrets=lambda: ['1//refresh-token-value']) as relay:
            status, _ = post(relay, STREAM, agent_call(text='leak 1//refresh-token-value'))
    assert status == 400 and fake.seen == []
    folder = next((tmp_path/'wire').iterdir())
    assert not (folder/'input.body').exists()
    assert json.loads((folder/'receipt.json').read_text())['refused'] == 'credential_in_model_request'


def test_egress_proxy_allowlist(tmp_path):
    echo = socket.socket()
    echo.bind(('127.0.0.1', 0))
    echo.listen(1)

    def serve():
        conn, _ = echo.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    target = echo.getsockname()
    connect = lambda address, timeout: socket.create_connection(target, timeout=timeout)
    with egress_proxy(log_path=tmp_path/'egress.jsonl', allowed_hosts={'oauth2.googleapis.com'}, connect=connect) as url:
        port = int(url.rsplit(':', 1)[1])
        for request, expected in ((b'CONNECT evil.example:443 HTTP/1.1\r\n\r\n', b'403'),
                                  (b'GET http://oauth2.googleapis.com/ HTTP/1.1\r\n\r\n', b'405'),
                                  (b'CONNECT oauth2.googleapis.com:80 HTTP/1.1\r\n\r\n', b'403')):
            with socket.create_connection(('127.0.0.1', port)) as s:
                s.sendall(request)
                assert expected in s.recv(100)
        with socket.create_connection(('127.0.0.1', port)) as s:
            s.sendall(b'CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n')
            assert b'200' in s.recv(100)
            s.sendall(b'ping')
            assert s.recv(100) == b'PING'
        import time
        time.sleep(.3)
    rows = [json.loads(l) for l in (tmp_path/'egress.jsonl').read_text().splitlines()]
    assert [r['decision'] for r in rows].count('refused') == 3
    assert any(r['decision'] == 'tunnel' and r['host'] == 'oauth2.googleapis.com' for r in rows)


def acp(update):
    return {'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': 's', 'update': update}}


def test_projection_maps_acp_tool_calls(tmp_path):
    ws = tmp_path/'workspace'
    records = [
        acp({'sessionUpdate': 'tool_call', 'toolCallId': 'c1', 'name': 'run_command', 'status': 'in_progress',
             'args': {'CommandLine': 'cd /workspace && python3 generate.py', 'WaitMsBeforeAsync': 10000}}),
        acp({'sessionUpdate': 'tool_call_update', 'toolCallId': 'c1', 'name': 'run_command', 'status': 'completed',
             'rawOutput': {'output': 'Command exited with code 0.\n\nCommand output:\nok', 'exit_code': 0}}),
        acp({'sessionUpdate': 'tool_call', 'toolCallId': 'c2', 'name': 'write_file', 'status': 'in_progress',
             'args': {'Path': '/workspace/generate.py', 'Content': 'x'}}),
        acp({'sessionUpdate': 'tool_call_update', 'toolCallId': 'c2', 'status': 'completed', 'rawOutput': {}}),
        acp({'sessionUpdate': 'tool_call', 'toolCallId': 'c3', 'name': 'read_file', 'status': 'completed',
             'args': {'Path': '/workspace/inspection.png'}}),
        acp({'sessionUpdate': 'tool_call', 'toolCallId': 'c4', 'name': 'run_command', 'status': 'completed',
             'args': {'CommandLine': 'false'}, 'rawOutput': {'output': 'Command exited with code 1.', 'exit_code': 1}}),
    ]
    rows, success, calls = agh.project(records, ws, 0, {'stopReason': 'end_turn'})
    assert success and calls == 4 and rows[-1] == {'type': 'turn.completed'}
    command = rows[0]['item']
    assert command['type'] == 'command_execution' and command['command'] == f'cd {ws} && python3 generate.py'
    assert command['exit_code'] == 0 and command['working_directory'] == str(ws) and command['aggregated_output'].endswith('ok')
    assert rows[1]['item'] == {'type': 'file_change', 'changes': [{'path': f'{ws}/generate.py'}], 'status': 'completed'}
    assert rows[2]['item']['name'] == 'antigravity_view_image'
    assert rows[3]['item']['status'] == 'failed' and rows[3]['item']['exit_code'] == 1
    _, success, _ = agh.project(records, ws, 0, {'stopReason': 'cancelled'})
    assert not success
    with pytest.raises(ValueError):
        agh.project([acp({'sessionUpdate': 'tool_call', 'toolCallId': 'x', 'name': 'search_web', 'status': 'completed'})],
                    ws, 0, {'stopReason': 'end_turn'})


def test_projection_preserves_network_and_outside_read_flags(tmp_path, monkeypatch):
    from pixelworld.audit import validate
    ws = tmp_path/'workspace'
    ws.mkdir()
    records = [acp({'sessionUpdate': 'tool_call', 'toolCallId': 'c1', 'name': 'run_command', 'status': 'completed',
                    'args': {'CommandLine': 'cat /tmp/x; curl https://example.com'},
                    'rawOutput': {'output': '', 'exit_code': 0}})]
    rows, _, _ = agh.project(records, ws, 0, {'stopReason': 'end_turn'})
    events = tmp_path/'events.jsonl'
    events.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    monkeypatch.setenv('PIXELWORLD_PRIVATE_TMP', '1')
    monkeypatch.setenv('PIXELWORLD_SANDBOX_ENFORCED', '1')
    report = validate(ws, events, 0, 'vision-agent', require_reports=False)
    assert report['network_commands'] == ['cat /tmp/x; curl https://example.com']
    assert report['outside_workspace_command_paths'] == ['/tmp/x']


def test_private_scratch_projection_keeps_auth_and_network_outside(tmp_path, monkeypatch):
    monkeypatch.setenv('PIXELWORLD_PRIVATE_TMP', '1')
    ws = tmp_path/'workspace'
    assert agh.host_view('/tmp/image.png', ws) == str(ws/'.agy-private-tmp/image.png')
    scratch = '/home/agent/.gemini/antigravity-cli/brain/conversation/preview.png'
    assert agh.host_view(scratch, ws) == str(ws/'.agy-private-brain/conversation/preview.png')
    auth = '/home/agent/.gemini/antigravity-cli/antigravity-oauth-token'
    assert agh.host_view(auth, ws) == auth
    assert agh.host_view('/home/other/secret', ws) == '/home/other/secret'
    assert agh.host_view('/tmp-other/secret', ws) == '/tmp-other/secret'
    assert agh.host_view('/workspace-other/secret', ws) == '/workspace-other/secret'
    assert agh.host_view('curl https://example.com', ws) == 'curl https://example.com'


def test_native_shell_launch_failure_is_not_a_backgrounded_success(tmp_path):
    item = agh.projected_item('run_command', {'CommandLine': 'echo hello'},
        {'output': 'Command failed to run: fork/exec /opt/agy/agy: no such file or directory.'},
        'completed', tmp_path)
    assert item['status'] == 'failed' and item['exit_code'] == 1
    assert not item.get('backgrounded')


def test_wire_tool_items_carry_returned_images():
    data = base64.b64encode(b'png').decode()
    request = {'request': {'contents': [
        {'role': 'model', 'parts': [{'functionCall': {'id': 'c1', 'name': 'read_file', 'args': {'Path': '/workspace/a.png'}}}]},
        {'role': 'user', 'parts': [{'functionResponse': {'id': 'c1', 'name': 'read_file', 'response': {'output': 'img'},
                                                         'parts': [{'inlineData': {'mimeType': 'image/png', 'data': data}}]}}]}]}}
    call, output = agh._wire_tool_items(request)
    assert call['type'] == 'function_call' and json.loads(call['arguments']) == {'Path': '/workspace/a.png'}
    assert output['output'][1] == {'type': 'input_image', 'image_url': 'data:image/png;base64,' + data}
    assert agh.usage_fields({'promptTokenCount': 10, 'candidatesTokenCount': 2, 'thoughtsTokenCount': 3,
                             'cachedContentTokenCount': 4}) == {
        'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 5, 'reasoning_output_tokens': 3, 'total_tokens': 15}


def test_prompt_attaches_first_frame_as_native_image(tmp_path):
    (tmp_path/'first_frame.png').write_bytes(b'\x89PNG')
    blocks = agh.prompt_blocks(tmp_path, 'task')
    assert blocks == [{'type': 'text', 'text': 'task'},
                      {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(b'\x89PNG').decode()}]
    (tmp_path/'first_frame.png').unlink()
    assert agh.prompt_blocks(tmp_path, 'task') == [{'type': 'text', 'text': 'task'}]


def test_permission_policy_and_settings():
    assert agh.permission_decision({'toolCall': {'name': 'run_command'}})[0] == 'allow'
    assert agh.permission_decision({'toolCall': {'name': 'invoke_subagent'}})[0] == 'reject'
    settings = agh.agy_settings()
    assert settings['enableTerminalSandbox'] is True and settings['allowNonWorkspaceAccess'] is False
    deny = settings['permissions']['deny']
    assert 'read_file(/home/agent)' in deny and 'write_file(/home/agent)' in deny and 'read_url(*)' in deny
    args = agh.client_arguments()
    assert args[0] == 'acp' and '--dangerously-skip-permissions' in args and '--sandbox' in args
    assert args[args.index('--agent') + 1] == 'pixelworld'
    assert args[args.index('--cloud_code_endpoint') + 1] == f'http://127.0.0.1:{ags.RELAY_PORT}'
    assert 'tools:\n  - read_file\n  - run_command\n  - write_file\n  - edit_file\n' in agh.AGENT_MARKDOWN


def test_prepare_home_copies_only_minimal_state(tmp_path):
    source = tmp_path/'agy'
    (source/'cache').mkdir(parents=True)
    (source/'antigravity-oauth-token').write_text('{"token": {"access_token": "real-host-secret-123"}}')
    (source/'cache'/'onboarding.json').write_text('{}')
    (source/'conversations').mkdir()
    (source/'conversations'/'old.db').write_text('history')
    home = tmp_path/'home'
    home.mkdir()
    prepared = agh.prepare_home(home, source)
    assert prepared['credential_placeholder']
    assert prepared['copied'] == ['cache/onboarding.json']
    for p in home.rglob('*'):
        if p.is_file():
            assert b'real-host-secret-123' not in p.read_bytes()
    files = sorted(str(p.relative_to(home)) for p in home.rglob('*') if p.is_file())
    assert files == ['.gemini/antigravity-cli/antigravity-oauth-token', '.gemini/antigravity-cli/cache/onboarding.json',
                     '.gemini/antigravity-cli/settings.json', '.gemini/config/agents/pixelworld.md']
    assert (home/'.gemini/antigravity-cli/antigravity-oauth-token').stat().st_mode & 0o077 == 0
    assert set(prepared['read_only']) == {home/'.gemini/antigravity-cli/settings.json', home/'.gemini/config/agents'}
    with pytest.raises(FileNotFoundError):
        agh.prepare_home(tmp_path/'other', tmp_path/'missing')


def test_sandbox_arguments_isolate_the_case(tmp_path):
    runtime, ws, home, client_dir = (tmp_path/n for n in ('runtime', 'ws', 'home', 'agy'))
    for path in (runtime/'bin', runtime/'lib', ws, home/'.gemini/config/agents', client_dir, tmp_path/'b1', tmp_path/'b2'):
        path.mkdir(parents=True, exist_ok=True)
    (home/'s.json').write_text('{}')
    (client_dir/'agy').write_text('')
    (runtime/'bin/bwrap').write_text('')
    args = ags.sandbox_arguments(client=client_dir/'agy', runtime=runtime, workspace=ws, home=home,
                                 bridges={'proxy': tmp_path/'b1', 'relay': tmp_path/'b2'},
                                 ports={'proxy': 8787, 'relay': 8788}, client_args=['acp'],
                                 env=ags.sandbox_environment(), read_only_home=[home/'s.json'])
    joined = ' '.join(args)
    assert '--unshare-net' in args and '--unshare-user' in args and '--clearenv' in args
    assert '--proc' not in args
    assert args[args.index('--tmpfs') + 1] == '/proc'
    writable = [args[i + 1] for i, a in enumerate(args) if a == '--bind']
    assert writable == [str(home.resolve()), str(ws.resolve())]
    assert ['--ro-bind', str((home/'s.json').resolve()), '/home/agent/s.json'] == args[args.index(str((home/'s.json').resolve())) - 1:][:3]
    assert args.index('--ro-bind', args.index(str(home.resolve()))) > args.index(str(home.resolve()))
    assert str(Path.home()/'.gemini') not in joined and str(Path.home()/'.claude') not in joined
    assert args[-2:] == ['/opt/agy/agy', 'acp']
    env = ags.sandbox_environment()
    assert env['AGY_CLI_DISABLE_AUTO_UPDATE'] == '1' and env['HTTPS_PROXY'] == 'http://127.0.0.1:8787'


def write_config(tmp_path, **model):
    base = {'key': 'gemini-4-neon-high', 'model': 'gemini-4-neon-high', 'reasoning_effort': 'high',
            'cli_backend': 'antigravity', 'cli_version': '1.5.18', 'tracks': ['vision-agent'],
            'provider': {'type': 'google_subscription', 'id': 'google_subscription', 'name': 'g'}}
    base.update(model)
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'protocol': 'pixelworld-physics_iq_720p24fps-native-source-v1',
                                'filesystem_policy': 'case-only-v1', 'output_token_budget': 1000000,
                                'max_attempts': 1, 'models': [base]}))
    return path


def test_config_accepts_google_subscription_route(tmp_path):
    config = load_config(write_config(tmp_path))
    model = config.models[0]
    assert model.cli_backend == 'antigravity' and model.provider.type == 'google_subscription'
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, cli_backend='claude_code', cli_version='2.1.304'))
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, cli_version='1.5.17'))
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, provider={'type': 'responses', 'id': 'a', 'name': 'b',
                                                      'base_url': 'http://x', 'env_key': 'K'}))
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, tracks=['vision-agent', 'text']))


def test_pinned_client_checks_antigravity_lock(tmp_path):
    from pixelworld.controlled_harness import pinned_client
    from pixelworld.util import sha256
    binary = tmp_path/'agy'
    binary.write_bytes(b'agy')
    locks = tmp_path/'locks.json'
    locks.write_text(json.dumps({'harnesses': {'antigravity': {'binary': str(binary), 'sha256': sha256(binary),
                                                               'version': '1.5.18'}}}))
    assert pinned_client(locks, 'antigravity')[0] == binary
    binary.write_bytes(b'updated')
    with pytest.raises(ValueError):
        pinned_client(locks, 'antigravity')
