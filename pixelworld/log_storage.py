"""A durable, capacity-bounded local buffer for asynchronous BN archival."""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid
import errno

DEFAULT_PROXY_LOG_MAX_BYTES = 64 * 1024**3
DEFAULT_PROXY_MIN_FREE_BYTES = 20 * 1024**3
CAPACITY_RECONCILE_INTERVAL_SECONDS = 30
DRAIN_MARKER = '.spool_drain'


def add_storage_arguments(parser):
    """One model-independent storage policy for standard bridge launches."""
    parser.add_argument('--spool-max-bytes', type=int, default=None,
                        help='Durable log directory capacity; standard default: 64 GiB')
    parser.add_argument('--min-free-bytes', type=int, default=DEFAULT_PROXY_MIN_FREE_BYTES,
                        help='Filesystem free-space reserve; default: 20 GiB')
    parser.add_argument('--async-cloud-logs', action='store_true',
                        help='Explicit legacy asynchronous diagnostics mode')


def resolve_storage_arguments(args, parser):
    if args.spool_max_bytes is None:
        args.spool_max_bytes = 0 if args.async_cloud_logs else DEFAULT_PROXY_LOG_MAX_BYTES
    if args.spool_max_bytes < 0 or args.min_free_bytes < 0:
        parser.error('Storage capacity and free-space reserve must be nonnegative')
    if args.async_cloud_logs and args.spool_max_bytes:
        parser.error('Do not combine asynchronous diagnostics with durable spool admission')
    return args


class StorageBackpressure(RuntimeError):
    pass


class SpoolBudget:
    def __init__(self, root: Path, max_bytes: int, min_free_bytes: int = 0):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes
        self.reservations: dict[str, int] = {}
        self.lock = threading.RLock()
        # One process owns mutable records. Archived removals are conservative
        # until explicit reconciliation or an admission needs reclaimed space.
        self._owner = (self.root / '.spool_writer.lock').open('a')
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._owner.close()
            raise RuntimeError('A durable log writer already owns this directory')
        self._used = None
        self._directory_stamp = None
        self._known_names = set()
        self._last_reconciled_at = 0.0

    def used_bytes(self):
        """Explicit reconciliation; ordinary managed writes update the ledger."""
        with self.lock:
            total = 0
            names = set()
            for entry in os.scandir(self.root):
                names.add(entry.name)
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        total += info.st_size
                except FileNotFoundError:
                    continue
            self._used = total
            self._known_names = names
            self._directory_stamp = self.root.stat().st_mtime_ns
            self._last_reconciled_at = time.monotonic()
            return total

    def _current_usage(self, *, check_namespace=False):
        if self._used is None:
            return self.used_bytes()
        if check_namespace and self.root.stat().st_mtime_ns != self._directory_stamp:
            if {entry.name for entry in os.scandir(self.root)} != self._known_names:
                return self.used_bytes()
            self._directory_stamp = self.root.stat().st_mtime_ns
        return self._used

    def _record_change(self, difference, *, created=None, removed=None):
        self._used += difference
        if created is not None:
            self._known_names.add(created)
        if removed is not None:
            self._known_names.discard(removed)
        self._directory_stamp = self.root.stat().st_mtime_ns

    def remove_record(self, name):
        """Remove a managed transient marker, maintaining exact capacity."""
        if Path(name).name != name:
            raise ValueError('Log record must be a basename')
        with self.lock:
            self._current_usage()
            target = self.root / name
            try:
                size = target.stat().st_size
                target.unlink()
            except FileNotFoundError:
                return
            self._record_change(-size, removed=name)

    def capacity_status(self, request_bytes: int = 0, *, refresh: bool = True) -> dict:
        """Inspect admission capacity without leasing it or starting inference.

        Completed log files still occupy capacity after a request releases its
        reservation. This is a snapshot; concurrent requests can change it.
        """
        if request_bytes < 0:
            raise ValueError('Request size must be nonnegative')
        reserve = request_bytes * 4 + 64 * 1024**2
        with self.lock:
            draining = (self.root / DRAIN_MARKER).exists()
            used = self.used_bytes() if refresh else self._current_usage(check_namespace=True)
            reserved = sum(self.reservations.values())
            free = shutil.disk_usage(self.root).free
            return {'ready': not draining and used + reserved + reserve <= self.max_bytes - 1024**2
                    and free >= self.min_free_bytes + reserve,
                    'draining': draining,
                    'used_bytes': used, 'reserved_bytes': reserved,
                    'max_bytes': self.max_bytes, 'free_bytes': free,
                    'min_free_bytes': self.min_free_bytes,
                    'request_bytes': request_bytes, 'required_reservation_bytes': reserve,
                    'active_reservations': len(self.reservations), 'snapshot_only': True,
                    'accounting': 'reconciled' if refresh else 'single-writer-ledger'}

    def acquire(self, request: str, request_bytes: int, timeout: float = 60):
        # Cover both request encodings, response/events and atomic-write peaks.
        reserve = request_bytes * 4 + 64 * 1024**2
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                draining = (self.root / DRAIN_MARKER).exists()
                if not draining:
                    used = self._current_usage(check_namespace=True)
                    free = shutil.disk_usage(self.root).free
                    if (used + sum(self.reservations.values()) + reserve > self.max_bytes - 1024**2
                            and time.monotonic() - self._last_reconciled_at >= CAPACITY_RECONCILE_INTERVAL_SECONDS):
                        # Waiting callers share reconciliation so leased writers
                        # can finish and release their reservations.
                        used = self.used_bytes()
                    if (used + sum(self.reservations.values()) + reserve
                            <= self.max_bytes - 1024**2
                            and free >= self.min_free_bytes + reserve):
                        self.reservations[request] = reserve
                        return
            if time.monotonic() >= deadline:
                reason = 'segment draining' if draining else 'buffer full'
                raise StorageBackpressure('storage_backpressure: durable log '+reason+'; no upstream sampling started')
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def release(self, request: str):
        with self.lock:
            self.reservations.pop(request, None)

    def write(self, request: str, name: str, data: bytes):
        while True:
            try:
                return self._write_once(request,name,data)
            except OSError as error:
                if (os.environ.get('PIXELWORLD_PERSIST_STORAGE_WAIT')!='1'
                        or error.errno not in (errno.EDQUOT,errno.ENOSPC,errno.EROFS)):
                    raise
                # The same admitted record waits without holding the writer
                # lock, sending another provider request or losing its lease.
                time.sleep(2)

    def _write_once(self, request: str, name: str, data: bytes):
        if Path(name).name != name:
            raise ValueError('Log record must be a basename')
        with self.lock:
            target = self.root / name
            previous = target.stat().st_size if target.exists() else 0
            remaining = self.reservations[request]
            other = sum(self.reservations.values()) - remaining
            if (len(data) > remaining or self._current_usage() + len(data) + other
                    > self.max_bytes - 1024**2):
                raise StorageBackpressure('storage_backpressure: record exceeds durable log reservation')
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o600)
                temporary.replace(target)
                self._record_change(len(data) - previous, created=name)
            except BaseException:
                self._used = None
                raise
            finally:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass
            self.reservations[request] -= max(0, len(data) - previous)

    def append_summary(self, request: str, record: dict):
        while True:
            try:
                return self._append_summary_once(request,record)
            except OSError as error:
                if (os.environ.get('PIXELWORLD_PERSIST_STORAGE_WAIT')!='1'
                        or error.errno not in (errno.EDQUOT,errno.ENOSPC,errno.EROFS)):
                    raise
                time.sleep(2)

    def _append_summary_once(self, request: str, record: dict):
        from .util import write_stream_bytes,fsync_stream
        data = (json.dumps(record, ensure_ascii=False) + '\n').encode()
        with self.lock:
            target = self.root / 'requests.jsonl'
            if target.exists() and target.stat().st_size >= 8 * 1024**2:
                rotated = self.root / ('requests_' + uuid.uuid4().hex + '.jsonl')
                target.rename(rotated)
                self._known_names.discard(target.name)
                self._known_names.add(rotated.name)
            remaining = self.reservations[request]
            if len(data) > remaining or self._current_usage() + len(data) > self.max_bytes:
                raise StorageBackpressure('storage_backpressure: summary exceeds log reservation')
            try:
                with target.open('ab',buffering=0) as stream:
                    write_stream_bytes(stream,data)
                    fsync_stream(stream)
            except BaseException:
                self._used = None
                raise
            self._record_change(len(data), created=target.name)
            self.reservations[request] -= len(data)

