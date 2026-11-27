"""Whole-process bwrap sandbox for the pinned Antigravity CLI (``agy``).

Same boundary as ``claude_process_sandbox`` (the Claude Code native route):

* new user, PID, IPC, UTS and network namespaces; ``--die-with-parent`` and
  ``--new-session``; a fresh ``/proc``;
* read-only system directories, the pinned runtime at ``/opt/runtime`` and
  the pinned ``agy`` binary (its own directory) at ``/opt/agy``; the case
  workspace is the only writable host directory (``/workspace``); HOME is a
  per-attempt private directory (``/home/agent``) holding a copy of the
  minimal agy login state; ``/tmp`` is a private tmpfs;
* no network namespace access. Two loopback ports inside the sandbox are
  bridged (unix sockets, read-only mounts under ``/run/pixelworld``) to

  - ``127.0.0.1:8788`` -> this case's Cloud Code recording relay (all model
    traffic, via ``agy acp --cloud_code_endpoint``);
  - ``127.0.0.1:8787`` -> an HTTPS CONNECT proxy that only tunnels to the
    allowlisted Google auth/user-info hosts (``HTTPS_PROXY``);

* the environment is cleared.

Differences from the Claude route: ``agy`` holds the operator's real OAuth
credential (a per-attempt copy of its token file, deleted after the attempt),
because its token refresh cannot be moved outside the client. Agent shell
commands additionally run inside agy's own terminal sandbox.
"""
from __future__ import annotations

from pathlib import Path

from .claude_process_sandbox import bwrap_binary

INNER_WORKSPACE = '/workspace'
INNER_HOME = '/home/agent'
INNER_RUNTIME = '/opt/runtime'
INNER_CLIENT = '/opt/agy'
INNER_BRIDGES = '/run/pixelworld'
SOCKET = 'relay.sock'
PROXY_PORT = 8787
RELAY_PORT = 8788

# Forward several loopback ports to unix sockets, then run argv.
LAUNCHER = r'''
import json, os, signal, socket, subprocess, sys, threading
routes, argv = json.loads(sys.argv[1]), sys.argv[2:]
def pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass
def serve(conn, path):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(path)
    except OSError:
        conn.close(); up.close(); return
    back = threading.Thread(target=pump, args=(up, conn), daemon=True)
    back.start(); pump(conn, up); back.join(); conn.close(); up.close()
def listen(port, path):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', port)); server.listen(64)
    def accept():
        while True:
            conn, _ = server.accept()
            threading.Thread(target=serve, args=(conn, path), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
for port, path in routes:
    listen(int(port), path)
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
child = subprocess.Popen(argv)
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, lambda s, f: child.send_signal(s))
code = child.wait()
sys.exit(code if code >= 0 else 128 - code)
'''


def sandbox_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    proxy = f'http://127.0.0.1:{PROXY_PORT}'
    env = {'PATH': f'{INNER_RUNTIME}/bin:/usr/bin:/bin', 'HOME': INNER_HOME, 'USER': 'agent', 'LOGNAME': 'agent',
           'SHELL': '/bin/bash', 'LANG': 'C.UTF-8',
           'TMPDIR': INNER_WORKSPACE + '/.tmp',
           'XDG_CONFIG_HOME': INNER_HOME + '/.config', 'XDG_DATA_HOME': INNER_HOME + '/.local/share',
           'XDG_CACHE_HOME': INNER_HOME + '/.cache', 'XDG_STATE_HOME': INNER_HOME + '/.local/state',
           'MPLCONFIGDIR': INNER_WORKSPACE + '/.tmp/matplotlib', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
           'PYTHONDONTWRITEBYTECODE': '1',
           'HTTPS_PROXY': proxy, 'https_proxy': proxy, 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
           'AGY_CLI_DISABLE_AUTO_UPDATE': '1'}
    env.update(extra or {})
    return env


def sandbox_arguments(*, client: Path, runtime: Path, workspace: Path, home: Path, bridges: dict[str, Path],
                      ports: dict[str, int], client_args: list[str], env: dict[str, str],
                      read_only_home: list[Path] = ()) -> list[str]:
    """bwrap argv running the launcher (bridges + agy) for one case.

    ``bridges`` maps a name to a host directory holding ``relay.sock``;
    ``ports`` maps the same names to the inner loopback port.
    """
    runtime, workspace, home = runtime.resolve(strict=True), workspace.resolve(strict=True), home.resolve(strict=True)
    command = [str(bwrap_binary(runtime)), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
               '--unshare-uts', '--hostname', 'pixelworld', '--die-with-parent', '--new-session',
               '--dev', '/dev', '--proc', '/proc']
    for root in ('/usr', '/bin', '/lib', '/lib64', '/etc'):
        if Path(root).exists():
            command += ['--ro-bind', root, root]
    for part in ('bin', 'lib'):
        command += ['--ro-bind', str(runtime / part), f'{INNER_RUNTIME}/{part}']
    command += ['--ro-bind', str(client.parent), INNER_CLIENT]
    for name, folder in sorted(bridges.items()):
        command += ['--ro-bind', str(folder), f'{INNER_BRIDGES}/{name}']
    command += ['--bind', str(home), INNER_HOME]
    for path in read_only_home:
        path = Path(path).resolve(strict=True)
        command += ['--ro-bind', str(path), str(Path(INNER_HOME) / path.relative_to(home))]
    command += ['--bind', str(workspace), INNER_WORKSPACE,
                '--tmpfs', '/tmp', '--remount-ro', '/', '--chdir', INNER_WORKSPACE, '--clearenv']
    for key, value in env.items():
        command += ['--setenv', key, value]
    import json
    routes = json.dumps([[ports[name], f'{INNER_BRIDGES}/{name}/{SOCKET}'] for name in sorted(bridges)])
    return command + [f'{INNER_RUNTIME}/bin/python', '-I', '-c', LAUNCHER, routes,
                      f'{INNER_CLIENT}/{client.name}', *client_args]
