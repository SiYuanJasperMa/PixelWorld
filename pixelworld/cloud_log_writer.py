"""Bounded asynchronous logging to cloud files; no worker-local disk spool.

This stores duplicate proxy diagnostics off the request path. The runner's
native CLI archive remains the primary trajectory record. An unclean shutdown
can interrupt pending diagnostics, so writer state and errors are explicit.
"""
from __future__ import annotations
from collections import deque
import json, os, threading, time, uuid
from pathlib import Path


class CloudLogWriter:
    def __init__(self, root: Path, max_queue_bytes: int = 512 * 1024**2,
                 write_delay: float = 0, workers: int = 4, durable: bool = False):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_queue_bytes = max_queue_bytes
        self.write_delay = write_delay
        if workers < 1:raise ValueError('Writer count must be positive')
        self.durable=durable
        self.active_keys=set()
        self.append_lock=threading.Lock()
        self.condition = threading.Condition()
        self.queue = deque()
        self.queued_bytes = 0
        self.pending = 0
        self.closed = False
        self.error = None
        self.writes = 0
        self.threads=[threading.Thread(target=self._run,daemon=True,name=f'cloud-proxy-log-writer-{i}')
                      for i in range(workers)]
        self.thread=self.threads[0]
        for thread in self.threads:thread.start()

    def submit(self, name: str, value, *, append: bool = False, raw: bool = False,
               timeout: float = 300, ordering_key: str | None = None):
        if Path(name).name != name:
            raise ValueError('Log name must be a basename')
        text = value if raw else json.dumps(value, ensure_ascii=False,separators=(',',':'))
        data = (text + ('\n' if append else '')).encode('utf-8')
        if len(data) > self.max_queue_bytes:
            raise RuntimeError('storage_backpressure: log record exceeds cloud logging queue')
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.queued_bytes + len(data) > self.max_queue_bytes:
                self._check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('storage_backpressure: cloud log writer queue full')
                self.condition.wait(min(remaining, .1))
            self._check()
            self.queue.append((name, data, append, ordering_key or name.split('.',1)[0]))
            self.queued_bytes += len(data)
            self.pending += 1
            self.condition.notify_all()

    def remove_marker(self, name: str):
        if Path(name).name != name:
            raise ValueError('Marker name must be a basename')
        with self.condition:
            self._check()
            self.queue.append((name, None, False, name.split('.',1)[0]))
            self.pending += 1
            self.condition.notify_all()

    def _check(self):
        if self.error:
            raise RuntimeError('storage_backpressure: cloud log writer failed: ' + self.error)
        if self.closed:
            raise RuntimeError('Cloud log writer is closed')

    def _write(self, name, data, append):
        if self.write_delay:
            time.sleep(self.write_delay)
        target = self.root / name
        if data is None:
            target.unlink(missing_ok=True)
            return
        if append:
            with self.append_lock:
                with target.open('ab') as f:
                    f.write(data); f.flush()
                    if self.durable:os.fsync(f.fileno())
            target.chmod(0o600)
        else:
            tmp = target.with_name(name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with tmp.open('wb') as f:
                    f.write(data); f.flush()
                    if self.durable:os.fsync(f.fileno())
                tmp.chmod(0o600); tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)

    def _run(self):
        while True:
            with self.condition:
                while True:
                    if not self.queue and self.closed:return
                    available=next((i for i,item in enumerate(self.queue) if item[3] not in self.active_keys),None)
                    if available is not None:
                        name,data,append,key=self.queue[available]
                        del self.queue[available]
                        self.active_keys.add(key)
                        break
                    self.condition.wait(.1)
            try:
                self._write(name, data, append)
                self.writes += 1
            except Exception as exc:
                with self.condition:
                    self.error = f'{type(exc).__name__}: {exc}'
            finally:
                with self.condition:
                    self.queued_bytes -= len(data) if data is not None else 0
                    self.pending -= 1
                    self.active_keys.remove(key)
                    self.condition.notify_all()

    def flush(self, timeout: float = 30):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.pending:
                if self.error:
                    raise RuntimeError(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Cloud log queue has not drained')
                self.condition.wait(min(remaining, .1))
            if self.error:
                raise RuntimeError(self.error)

    def status(self):
        with self.condition:
            return {'pending_records': self.pending, 'queued_bytes': self.queued_bytes,
                    'max_queue_bytes': self.max_queue_bytes, 'completed_writes': self.writes,
                    'error': self.error, 'path': str(self.root), 'worker_local_spool': False,
                    'workers':len(self.threads),'diagnostic_fsync':self.durable}

    def close(self):
        self.flush()
        with self.condition:
            self.closed = True; self.condition.notify_all()
        for thread in self.threads:thread.join(timeout=2)
