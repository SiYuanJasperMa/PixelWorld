"""Whole-process sandbox for pinned native Antigravity.

The case workspace, private HOME, private /tmp, system libraries, and pinned
runtime are the only mounts. HOME contains no actual OAuth credential. Host
/proc is absent; an empty /proc provides only a client-executable symlink for
Go's executable-path lookup. A Unix-socket bridge reaches the fixed-upstream
host relay. The terminal sandbox further restricts agent shell commands.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from .claude_process_sandbox import bwrap_binary

INNER_WORKSPACE = '/workspace'
INNER_HOME = '/home/agent'
INNER_RUNTIME = '/opt/runtime'
INNER_CLIENT = '/opt/agy'
INNER_COMMAND_GUARD = '/opt/pixelworld-command'
INNER_BRIDGES = '/run/pixelworld'
SOCKET = 'relay.sock'
PROXY_PORT = 8787
RELAY_PORT = 8788

COMMAND_GUARD = r'''#!/opt/runtime/bin/python
import ctypes, errno, os, platform, sys
libc = ctypes.CDLL(None, use_errno=True)
if libc.syscall(436,3,ctypes.c_uint(0xffffffff),0):
    os.closerange(3,65536)
class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]
arch = platform.machine()
if arch == 'x86_64':
    audit_arch = 0xc000003e
    denied = [41,42,43,49,50,288,101,310,311,425,426,427,438]
elif arch == 'aarch64':
    audit_arch = 0xc00000b7
    denied = [198,200,201,202,203,242,117,270,271,425,426,427,438]
else:
    raise RuntimeError('Command network guard requires Linux x86_64 or aarch64')
ops = [(0x20,0,0,4),(0x15,1,0,audit_arch),(0x06,0,0,0x80000000),(0x20,0,0,0)]
if arch == 'x86_64':
    ops += [(0x45,0,1,0x40000000),(0x06,0,0,0x00050000 | errno.EPERM)]
for number in denied:
    ops += [(0x15,0,1,number),(0x06,0,0,0x00050000 | errno.EPERM)]
ops += [(0x06,0,0,0x7fff0000)]
filters = (Filter * len(ops))(*(Filter(*op) for op in ops))
program = Program(len(ops), filters)
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(38,1,0,0,0) or libc.prctl(22,2,ctypes.byref(program),0,0):
    raise OSError(ctypes.get_errno(), 'Could not enforce the command network guard')
# socketpair remains available for private process IPC. Named sockets,
# connections, io_uring network operations and parent descriptor theft are blocked.
real = '/opt/pixelworld-command/sh.real' if os.path.basename(sys.argv[0]) == 'sh' else '/opt/pixelworld-command/bash.real'
os.execv(real, [real, *sys.argv[1:]])
'''


def prepare_command_guard(path: Path) -> Path:
    """Supply real system shells and a namespace guard outside the run directory."""
    path.mkdir(mode=0o700)
    for name in ('bash', 'sh'):
        shutil.copyfile(Path('/bin') / name, path / (name + '.real'))
        (path / (name + '.real')).chmod(0o755)
    (path / 'shell').write_text(COMMAND_GUARD)
    (path / 'shell').chmod(0o755)
    return path

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
                      read_only_home: list[Path] = (), command_guard: Path | None = None) -> list[str]:
    """bwrap argv running the launcher (bridges + agy) for one case.

    ``bridges`` maps a name to a host directory holding ``relay.sock``;
    ``ports`` maps the same names to the inner loopback port.
    """
    runtime, workspace, home = runtime.resolve(strict=True), workspace.resolve(strict=True), home.resolve(strict=True)
    command = [str(bwrap_binary(runtime)), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
               '--unshare-uts', '--hostname', 'pixelworld', '--die-with-parent', '--new-session',
               '--dev', '/dev', '--tmpfs', '/proc', '--dir', '/proc/self',
               '--symlink', f'{INNER_CLIENT}/{client.name}', '/proc/self/exe']
    for root in ('/usr', '/bin', '/lib', '/lib64', '/etc'):
        if Path(root).exists():
            command += ['--ro-bind', root, root]
    for part in ('bin', 'lib'):
        command += ['--ro-bind', str(runtime / part), f'{INNER_RUNTIME}/{part}']
    command += ['--ro-bind', str(client.parent), INNER_CLIENT]
    if command_guard is not None:
        command_guard = command_guard.resolve(strict=True)
        command += ['--ro-bind', str(command_guard), INNER_COMMAND_GUARD]
        for name in ('bash', 'sh'):
            for parent in ('/bin', '/usr/bin'):
                target = Path(parent) / name
                if target.exists():
                    command += ['--ro-bind', str(command_guard / 'shell'), str(target)]
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
