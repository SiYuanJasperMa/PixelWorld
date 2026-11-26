import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixelworld import claude_process_sandbox as cps
from pixelworld.config import Provider
from pixelworld.controlled_harness import (NATIVE_TOOLS, native_claude_args, native_claude_environment, project,
                                           subscription_tool_mode)


def layout(tmp_path):
    client = tmp_path / 'cc/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    client.parent.mkdir(parents=True)
    client.write_bytes(b'')
    runtime = tmp_path / 'runtime'
    for part in ('bin', 'lib'):
        (runtime / part).mkdir(parents=True)
    (runtime / 'bin/bwrap').write_bytes(b'')
    dirs = {name: tmp_path / name for name in ('workspace', 'home', 'bridge')}
    for d in dirs.values():
        d.mkdir()
    return client, runtime, dirs


def pairs(argv, flag):
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


def test_native_command_sandboxes_whole_client(tmp_path):
    client, runtime, d = layout(tmp_path)
    env = native_claude_environment()
    args = cps.sandbox_arguments(client=client, runtime=runtime, workspace=d['workspace'], home=d['home'],
                                 bridge=d['bridge'], port=cps.INNER_PORT,
                                 claude_args=native_claude_args('claude-opus-5-5', 'high'), env=env)
    for flag in ('--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent', '--new-session',
                 '--clearenv'):
        assert flag in args
    # The only writable host binds are the workspace and the private home.
    assert pairs(args, '--bind') == [(str(d['home'].resolve()), cps.INNER_HOME),
                                     (str(d['workspace'].resolve()), cps.INNER_WORKSPACE)]
    ro = dict(pairs(args, '--ro-bind'))
    assert ro[str(client.parents[3])] == '/opt/claude-code/node_modules'
    assert ro[str(d['bridge'])] == cps.INNER_BRIDGE
    assert ro[str(runtime / 'bin')] == '/opt/runtime/bin'
    assert set(ro) - {str(client.parents[3]), str(d['bridge']), str(runtime / 'bin'), str(runtime / 'lib')} <= {
        '/usr', '/bin', '/lib', '/lib64', '/etc'}
    assert str(Path.home()) not in ro and str(tmp_path) not in ro
    setenv = dict(pairs(args, '--setenv'))
    assert setenv['ANTHROPIC_BASE_URL'] == f'http://127.0.0.1:{cps.INNER_PORT}'
    assert setenv['CLAUDE_CODE_OAUTH_TOKEN'] == cps.PLACEHOLDER_TOKEN
    assert setenv['BASH_DEFAULT_TIMEOUT_MS'] == setenv['BASH_MAX_TIMEOUT_MS'] == '600000'
    assert setenv['HOME'] == cps.INNER_HOME and 'ANTHROPIC_API_KEY' not in setenv
    tail = args[args.index('/opt/runtime/bin/python'):]
    assert tail[4:7] == [f'{cps.INNER_BRIDGE}/{cps.BRIDGE_SOCKET}', str(cps.INNER_PORT),
                         '/opt/claude-code/node_modules/@anthropic-ai/claude-code/bin/claude.exe']
    claude = tail[7:]
    tools = ','.join(NATIVE_TOOLS)
    assert claude[claude.index('--tools') + 1] == tools == claude[claude.index('--allowedTools') + 1]
    assert set(tools.split(',')) == {'Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep'}
    assert claude[claude.index('--permission-mode') + 1] == 'bypassPermissions'
    assert claude[claude.index('--setting-sources') + 1] == ''
    for flag in ('-p', '--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence', '--verbose'):
        assert flag in claude
    assert '--mcp-config' not in claude
    command = cps.sandbox_command(args, tmp_path / 'args')
    options = (tmp_path / 'args').read_bytes().split(b'\0')[:-1]
    assert [o.decode() for o in options] == args[1:args.index('/opt/runtime/bin/python')]
    assert str(d['workspace'].resolve()) not in ' '.join(command)


def test_bridge_forwards_unix_socket_to_relay():
    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = self.path.encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with cps.relay_bridge(f'http://127.0.0.1:{server.server_port}') as folder:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(folder / cps.BRIDGE_SOCKET))
                s.sendall(b'GET /v1/ping HTTP/1.1\r\nHost: x\r\n\r\n')
                reply = b''
                while chunk := s.recv(4096):
                    reply += chunk
        assert reply.endswith(b'/v1/ping') and not folder.exists()
        with pytest.raises(ValueError):
            with cps.relay_bridge('http://example.com:80'):
                pass
    finally:
        server.shutdown()


def test_native_projection_maps_sandbox_paths_and_rejects_other_tools(tmp_path):
    def run(name, args, result, error=False):
        return [{'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't', 'name': name,
                                                                'input': args}]}},
                {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't',
                                                           'content': result, 'is_error': error}]}},
                {'type': 'result', 'subtype': 'success', 'is_error': False}]
    rows, ok, calls = project(run('Write', {'file_path': '/workspace/a.py', 'content': 'x'}, 'ok'),
                              'claude_code', tmp_path, 0, tool_mode='native')
    assert ok and calls == 1 and rows[0]['item']['changes'] == [{'path': str(tmp_path / 'a.py')}]
    rows, ok, _ = project(run('Bash', {'command': 'python /workspace/g.py'}, 'Exit code 3\nboom', True),
                          'claude_code', tmp_path, 0, tool_mode='native')
    item = rows[0]['item']
    assert item['command'] == f'python {tmp_path}/g.py' and item['exit_code'] == 3 and item['status'] == 'failed'
    rows, _, _ = project(run('Read', {'file_path': '/workspace/first_frame.png'}, [{'type': 'image'}]),
                         'claude_code', tmp_path, 0, tool_mode='native')
    assert rows[0]['item']['name'] == 'claude_code_view_image'
    for name in ('WebFetch', 'Task', 'mcp__pixelworld__bash'):
        with pytest.raises(ValueError):
            project(run(name, {}, 'x'), 'claude_code', tmp_path, 0, tool_mode='native')
    with pytest.raises(ValueError):
        project(run('Bash', {'command': 'ls'}, 'x'), 'claude_code', tmp_path, 0)


def test_subscription_tool_mode_defaults_to_native():
    model = lambda **kw: SimpleNamespace(provider=Provider(type='anthropic_subscription', env_key='K', **kw))
    assert subscription_tool_mode(model()) == 'native'
    assert subscription_tool_mode(model(tool_mode='mcp')) == 'mcp'
