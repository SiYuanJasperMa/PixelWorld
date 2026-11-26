"""Whole-process bwrap sandbox for pinned Claude Code with its native tools.

The complete Claude Code process tree (the client and every Bash/Read/Write/
Edit/Glob/Grep child it starts) runs in one bubblewrap sandbox:

* new user, PID, IPC, UTS and network namespaces; ``--die-with-parent`` and
  ``--new-session``; a fresh ``/proc`` that only shows sandbox processes
  (the pinned Bun executable needs ``/proc/self/exe``), no host ``/proc``;
* read-only system directories, the pinned runtime at ``/opt/runtime``, the
  pinned Claude Code install tree at ``/opt/claude-code``; the case workspace
  is the only writable host directory (``/workspace``); HOME/XDG live in a
  per-attempt private directory (``/home/agent``), ``/tmp`` is a tmpfs;
* no network: the only way out is the loopback bridge below, which reaches
  the case's Messages relay and nothing else;
* the environment is cleared; the client holds a placeholder OAuth token,
  the relay outside the sandbox substitutes the real one.

Bridge: outside, a unix socket in a private directory forwards each
connection to the relay's loopback TCP port. That directory is bind-mounted
read-only at ``/run/pixelworld``. Inside, a small launcher (pinned runtime
Python) listens on ``127.0.0.1:<port>`` and forwards each connection to the
unix socket, then starts Claude Code with ``ANTHROPIC_BASE_URL`` pointing at
that loopback port.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import tempfile
from threading import Thread

INNER_WORKSPACE = '/workspace'
INNER_HOME = '/home/agent'
INNER_RUNTIME = '/opt/runtime'
INNER_CLIENT_ROOT = '/opt/claude-code'
INNER_BRIDGE = '/run/pixelworld'
BRIDGE_SOCKET = 'relay.sock'
INNER_PORT = 8787  # the sandbox has its own empty network namespace
PLACEHOLDER_TOKEN = 'pixelworld-placeholder-not-a-credential'

LAUNCHER = r'''
import signal, socket, subprocess, sys, threading
path, port, argv = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
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
def serve(conn):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(path)
    except OSError:
        conn.close(); up.close(); return
    back = threading.Thread(target=pump, args=(up, conn), daemon=True)
    back.start(); pump(conn, up); back.join(); conn.close(); up.close()
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', port)); server.listen(64)
def accept():
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
threading.Thread(target=accept, daemon=True).start()
import os
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
child = subprocess.Popen(argv)
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, lambda s, f: child.send_signal(s))
code = child.wait()
sys.exit(code if code >= 0 else 128 - code)
'''


def bwrap_binary(runtime: Path) -> Path:
    # Prefer the system binary: the sandbox's PID 1 cmdline then names no host path.
    for candidate in (Path('/usr/bin/bwrap'), runtime / 'bin/bwrap'):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('The whole-process sandbox needs bwrap')


def client_install_root(client: Path) -> Path:
    """The pinned install tree: the nearest ``node_modules`` ancestor, else the binary's folder."""
    for parent in client.parents:
        if parent.name == 'node_modules':
            return parent
    return client.parent


def _pump(src, dst):
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


@contextmanager
def relay_bridge(relay_url: str):
    """Yield a private directory holding a unix socket that forwards to ``relay_url`` (loopback)."""
    host, port = relay_url.removeprefix('http://').rstrip('/').rsplit(':', 1)
    if host != '127.0.0.1':
        raise ValueError('The bridge only forwards to a loopback relay')
    # Unix socket paths are limited to ~108 bytes, so use a short private dir.
    folder = Path(tempfile.mkdtemp(prefix='pw-bridge-'))
    os.chmod(folder, 0o700)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(folder / BRIDGE_SOCKET))
    server.listen(64)
    closed = [False]

    def serve(conn):
        try:
            up = socket.create_connection((host, int(port)))
        except OSError:
            conn.close()
            return
        back = Thread(target=_pump, args=(up, conn), daemon=True)
        back.start()
        _pump(conn, up)
        back.join()
        conn.close()
        up.close()

    def accept():
        while not closed[0]:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            Thread(target=serve, args=(conn,), daemon=True).start()

    Thread(target=accept, daemon=True).start()
    try:
        yield folder
    finally:
        closed[0] = True
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()
        shutil.rmtree(folder, ignore_errors=True)


def sandbox_environment(port: int = INNER_PORT, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {'PATH': f'{INNER_RUNTIME}/bin:/usr/bin:/bin', 'HOME': INNER_HOME, 'USER': 'agent', 'LOGNAME': 'agent',
           'SHELL': '/bin/bash', 'LANG': 'C.UTF-8',
           # Codex main: TMPDIR inside the case and /tmp excluded from writes.
           'TMPDIR': INNER_WORKSPACE + '/.tmp', 'CLAUDE_CODE_TMPDIR': INNER_WORKSPACE + '/.tmp',
           'XDG_CONFIG_HOME': INNER_HOME + '/.config', 'XDG_DATA_HOME': INNER_HOME + '/.local/share',
           'XDG_CACHE_HOME': INNER_HOME + '/.cache', 'XDG_STATE_HOME': INNER_HOME + '/.local/state',
           'MPLCONFIGDIR': INNER_WORKSPACE + '/.tmp/matplotlib', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
           'PYTHONDONTWRITEBYTECODE': '1',
           'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{port}', 'CLAUDE_CODE_OAUTH_TOKEN': PLACEHOLDER_TOKEN}
    env.update(extra or {})
    return env


def sandbox_arguments(*, client: Path, runtime: Path, workspace: Path, home: Path, bridge: Path,
                      port: int, claude_args: list[str], env: dict[str, str]) -> list[str]:
    """bwrap argv running the launcher (bridge + Claude Code) for one case."""
    runtime, workspace, home = runtime.resolve(strict=True), workspace.resolve(strict=True), home.resolve(strict=True)
    install = client_install_root(client)
    inner_client = Path(INNER_CLIENT_ROOT) / install.name / client.relative_to(install)
    command = [str(bwrap_binary(runtime)), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
               '--unshare-uts', '--hostname', 'pixelworld', '--die-with-parent', '--new-session',
               '--dev', '/dev', '--proc', '/proc']
    for root in ('/usr', '/bin', '/lib', '/lib64', '/etc'):
        if Path(root).exists():
            command += ['--ro-bind', root, root]
    for part in ('bin', 'lib'):
        command += ['--ro-bind', str(runtime / part), f'{INNER_RUNTIME}/{part}']
    command += ['--ro-bind', str(install), str(Path(INNER_CLIENT_ROOT) / install.name),
                '--ro-bind', str(bridge), INNER_BRIDGE,
                '--bind', str(home), INNER_HOME,
                '--bind', str(workspace), INNER_WORKSPACE,
                '--tmpfs', '/tmp', '--remount-ro', '/', '--chdir', INNER_WORKSPACE, '--clearenv']
    for key, value in env.items():
        command += ['--setenv', key, value]
    return command + [f'{INNER_RUNTIME}/bin/python', '-I', '-c', LAUNCHER,
                      f'{INNER_BRIDGE}/{BRIDGE_SOCKET}', str(port), str(inner_client), *claude_args]


def sandbox_command(args: list[str], args_file: Path) -> list[str]:
    """Launch ``args`` with bwrap reading its options from a file descriptor.

    The sandbox's PID 1 is bwrap itself; passing the options through
    ``--args`` keeps host paths (bind sources) out of its visible cmdline.
    ``args_file`` must be outside every sandbox mount.
    """
    split = args.index(f'{INNER_RUNTIME}/bin/python')
    bwrap, options, inner = args[0], args[1:split], args[split:]
    args_file.write_bytes(b''.join(a.encode() + b'\0' for a in options))
    args_file.chmod(0o600)
    # bwrap 0.6 parses only options from --args; the (sandbox-path-only) command stays on argv.
    return ['/bin/sh', '-c', 'f=$1; shift; exec "$0" --args 3 "$@" 3<"$f"', bwrap, str(args_file), *inner]
