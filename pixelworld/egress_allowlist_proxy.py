"""Case-scoped HTTPS CONNECT proxy with a host allowlist and an egress log.

The Antigravity CLI authenticates with the operator's Google OAuth login and
talks gRPC/HTTPS to Google endpoints itself, so (unlike the Claude route) its
traffic cannot be relayed verbatim: TLS terminates inside the client. The
sandboxed client therefore gets no network namespace access at all except
this proxy (reached through the same unix-socket bridge as the Claude relay),
which only tunnels ``CONNECT host:443`` to allowlisted hosts and records each
tunnel (host, port, start/end time, byte counts; never payloads) in
``egress.jsonl``. Anything else is refused locally and recorded as refused.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import socket
import threading
import time

from .util import utc_now

# Hosts the pinned agy 1.5.18 needs for an OAuth (consumer) print-mode run.
ANTIGRAVITY_HOSTS = frozenset({
    'daily-cloudcode-pa.googleapis.com',   # model + code-assist API (observed)
    'cloudcode-pa.googleapis.com',
    'oauth2.googleapis.com',               # access-token refresh
    'www.googleapis.com',                  # token info / userinfo
})


def _pump(src, dst, counter, key):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            counter[key] += len(data)
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _read_head(conn, limit=16384):
    data = b''
    while b'\r\n\r\n' not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return None, b''
        data += chunk
        if len(data) > limit:
            return None, b''
    head, rest = data.split(b'\r\n\r\n', 1)
    return head.decode('latin-1'), rest


@contextmanager
def egress_proxy(*, log_path, allowed_hosts=ANTIGRAVITY_HOSTS, ports=(443,), connect=socket.create_connection):
    """Yield ``http://127.0.0.1:<port>`` of a CONNECT-only allowlist proxy."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(64)
    lock = threading.Lock()
    closed = [False]
    log = open(log_path, 'a', buffering=1)

    def record(row):
        with lock:
            if not log.closed:
                log.write(json.dumps(row, sort_keys=True) + '\n')

    def serve(conn):
        started = time.monotonic()
        head, rest = None, b''
        try:
            head, rest = _read_head(conn)
        except OSError:
            pass
        line = (head or '').split('\r\n', 1)[0]
        parts = line.split()
        if len(parts) != 3 or parts[0] != 'CONNECT':
            record({'utc': utc_now(), 'decision': 'refused', 'reason': 'not a CONNECT request',
                    'method': parts[0] if parts else None})
            try:
                conn.sendall(b'HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            conn.close()
            return
        host, _, port = parts[1].rpartition(':')
        host = host.strip('[]').lower()
        try:
            port = int(port)
        except ValueError:
            port = -1
        if host not in allowed_hosts or port not in ports:
            record({'utc': utc_now(), 'decision': 'refused', 'reason': 'host not allowlisted', 'host': host, 'port': port})
            try:
                conn.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            conn.close()
            return
        try:
            up = connect((host, port), timeout=30)
            up.settimeout(None)
        except OSError as error:
            record({'utc': utc_now(), 'decision': 'upstream_error', 'host': host, 'port': port, 'error': type(error).__name__})
            try:
                conn.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            conn.close()
            return
        opened = utc_now()
        counter = {'sent': 0, 'received': 0}
        try:
            conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            if rest:
                counter['sent'] += len(rest)
                up.sendall(rest)
        except OSError:
            conn.close(); up.close()
            return
        back = threading.Thread(target=_pump, args=(up, conn, counter, 'received'), daemon=True)
        back.start()
        _pump(conn, up, counter, 'sent')
        back.join()
        conn.close()
        up.close()
        record({'utc': opened, 'closed_utc': utc_now(), 'decision': 'tunnel', 'host': host, 'port': port,
                'bytes_sent': counter['sent'], 'bytes_received': counter['received'],
                'seconds': round(time.monotonic() - started, 3)})

    def accept():
        while not closed[0]:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.getsockname()[1]}'
    finally:
        closed[0] = True
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()
        with lock:
            log.close()
