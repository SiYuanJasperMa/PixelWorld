"""Private metadata index of one explicitly selected proxy requests.jsonl.

No payload discovery, provider requests, or credential access occurs here. Run
membership requires a recorded thread ID or a direct response ID ledger entry.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

RID = re.compile(r'resp_[A-Za-z0-9]+\Z')
MAX_LINE_BYTES = 1024 * 1024
ANCHOR_BYTES = 4096


def _timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        elif isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            result = parsed.timestamp()
        else:
            return None
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _private_file(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Index and lock must be regular files')
    os.fchmod(fd, 0o600)
    return fd


def _anchor(handle, offset):
    handle.seek(max(0, offset - ANCHOR_BYTES))
    return hashlib.sha256(handle.read(min(offset, ANCHOR_BYTES))).hexdigest()


def _lines(handle, limit):
    """Yield complete bounded records; leave an unfinished final line unconsumed."""
    while handle.tell() < limit:
        start = handle.tell()
        data = handle.readline(min(MAX_LINE_BYTES + 1, limit - start))
        oversized = len(data) > MAX_LINE_BYTES
        if oversized and not data.endswith(b'\n'):
            while handle.tell() < limit:
                tail = handle.readline(min(MAX_LINE_BYTES, limit - handle.tell()))
                if tail.endswith(b'\n'):
                    break
            else:
                handle.seek(start)
                return
            yield None, handle.tell()
        elif data.endswith(b'\n'):
            yield None if oversized else data, handle.tell()
        else:
            handle.seek(start)
            return


def update_index(log_root, db_path, *, batch_size=1000):
    """Index complete appended lines, checkpointing rows and offsets atomically.

    log_root is a proxy directory or the explicit requests.jsonl path. A database
    belongs to exactly one resolved source path. Inode changes, shrinkage, and
    changed checkpoint bytes rebuild that source's index. The source size at
    entry bounds each update; incomplete final lines are retried next time.
    """
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('batch_size must be a positive integer')
    source = Path(log_root)
    if source.is_dir():
        source = source / 'requests.jsonl'
    source = source.absolute()
    if source.name != 'requests.jsonl' or source.is_symlink() or source.resolve() != source:
        raise ValueError('Supply one real requests.jsonl source')
    database = Path(db_path).absolute()
    if database.resolve() != database or database == source:
        raise ValueError('Supply a separate real private index path')
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = _private_file(database.with_suffix(database.suffix + '.lock'))
    with os.fdopen(lock_fd, 'r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.close(_private_file(database))
        with closing(sqlite3.connect(database)) as connection, os.fdopen(
                os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Proxy request source must be a regular file')
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('PRAGMA synchronous=FULL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS source_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    source_path TEXT NOT NULL, device INTEGER NOT NULL,
                    inode INTEGER NOT NULL, offset INTEGER NOT NULL,
                    anchor TEXT NOT NULL, total_lines INTEGER NOT NULL,
                    invalid_lines INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, thread_id TEXT, started REAL,
                    model TEXT, native_model TEXT);
                CREATE INDEX IF NOT EXISTS requests_thread ON requests(thread_id);
            ''')
            state = connection.execute('SELECT source_path, device, inode, offset, anchor, '
                                       'total_lines, invalid_lines FROM source_state').fetchone()
            if state and state[0] != str(source):
                raise ValueError('Index is bound to a different explicit proxy source')
            offset, total, invalid = (state[3], state[5], state[6]) if state else (0, 0, 0)
            reason = None
            if state:
                if state[1:3] != (info.st_dev, info.st_ino):
                    reason = 'inode_changed'
                elif info.st_size < offset:
                    reason = 'source_truncated'
                elif _anchor(handle, offset) != state[4]:
                    reason = 'checkpoint_bytes_changed'
            if reason:
                with connection:
                    connection.execute('DELETE FROM requests')
                    connection.execute('DELETE FROM source_state')
                offset = total = invalid = 0
            start_offset = offset
            counts = Counter(new_lines=0, invalid_lines=0, rows_written=0)

            def checkpoint():
                connection.execute('INSERT OR REPLACE INTO source_state VALUES (1, ?, ?, ?, ?, ?, ?, ?)',
                    (str(source), info.st_dev, info.st_ino, offset, _anchor(handle, offset), total, invalid))
                connection.commit()
                handle.seek(offset)

            handle.seek(offset)
            batch = 0
            for line, end in _lines(handle, info.st_size):
                offset = end
                total += 1
                counts['new_lines'] += 1
                try:
                    row = json.loads(line) if line is not None else None
                    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not RID.fullmatch(row['id']):
                        raise ValueError('Invalid request ID')
                    # Keep only routing metadata; request bodies and arbitrary
                    # metadata dictionaries never enter this private cache.
                    connection.execute('INSERT OR REPLACE INTO requests VALUES (?, ?, ?, ?, ?)',
                        (row['id'], _text(row.get('thread_id')), _timestamp(row.get('started')),
                         _text(row.get('model')), _text(row.get('native_model'))))
                    counts['rows_written'] += 1
                except (ValueError, TypeError, UnicodeError):
                    invalid += 1
                    counts['invalid_lines'] += 1
                batch += 1
                if batch >= batch_size:
                    checkpoint()
                    batch = 0
            checkpoint()
            indexed, unthreaded = connection.execute(
                'SELECT count(*), count(*) FILTER (WHERE thread_id IS NULL) FROM requests').fetchone()
            return {**dict(counts), 'indexed_requests': indexed, 'indexed_without_thread': unthreaded,
                    'total_lines': total, 'total_invalid_lines': invalid, 'offset': offset,
                    'bytes_consumed': offset - start_offset, 'source_bytes': info.st_size,
                    'pending_bytes': max(0, info.st_size - offset), 'reset_reason': reason}


def _read_rows(path, counts, counter):
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        return
    with path.open('rb') as handle:
        for line, _ in _lines(handle, os.fstat(handle.fileno()).st_size):
            try:
                row = json.loads(line) if line is not None else None
                if not isinstance(row, dict):
                    raise ValueError('Expected object')
                yield row
            except (ValueError, TypeError, UnicodeError):
                counts[counter] += 1


def request_ids_for_run(db_path, run_or_manifest, *, run_path=None, exact_model=None):
    """Return exact IDs with association evidence and honest missing counters.

    Accept a run directory, manifest path, or dict plus optional run_path for
    its logs and usage ledger. Conflicting explicit thread sources disable
    thread expansion. Direct IDs remain included even when the index lacks
    them. The manifest time window applies after exact thread association. An
    optional exact_model filter accepts the logged public or native model;
    manifest model aliases do not suppress an explicitly associated thread.
    """
    counts = Counter(invalid_ledger_lines=0, invalid_event_lines=0)
    if isinstance(run_or_manifest, dict):
        manifest = run_or_manifest
        run = Path(run_path).resolve() if run_path is not None else None
    else:
        path = Path(run_or_manifest).resolve()
        run = path.parent if path.is_file() else path
        manifest = json.loads((path if path.is_file() else path / 'run_manifest.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('Run manifest must be an object')
    threads = {}

    def add_thread(value, source):
        value = _text(value)
        if value is not None:
            threads.setdefault(value, source)

    full_trace = manifest.get('full_trace')
    if isinstance(full_trace, dict):
        add_thread(full_trace.get('thread_id'), 'manifest.full_trace.thread_id')
    direct = set()

    def add_ids(row):
        values = [row.get('response_id')]
        ids = row.get('response_ids')
        if isinstance(ids, list):
            values.extend(ids)
        for value in values:
            if isinstance(value, str) and RID.fullmatch(value):
                direct.add(value)

    add_ids(manifest)
    if run is not None:
        status_path = run / 'logs/full_trace_status.json'
        if status_path.is_file() and not status_path.is_symlink() and status_path.resolve() == status_path:
            try:
                status = json.loads(status_path.read_text())
                if isinstance(status, dict):
                    add_thread(status.get('thread_id'), 'logs/full_trace_status.json')
            except (ValueError, UnicodeError):
                counts['invalid_trace_status'] += 1
        for event in _read_rows(run / 'logs/events.jsonl', counts, 'invalid_event_lines'):
            if event.get('type') == 'thread.started':
                add_thread(event.get('thread_id'), 'logs/events.jsonl:thread.started')
        for row in _read_rows(run / 'recording/api_usage.jsonl', counts, 'invalid_ledger_lines'):
            add_ids(row)
    thread_id = next(iter(threads)) if len(threads) == 1 else None
    counts['ambiguous_thread_identity'] = int(len(threads) > 1)
    counts['missing_thread_identity'] = int(not threads)
    start, end = _timestamp(manifest.get('started_utc')), _timestamp(manifest.get('completed_utc'))
    model = _text(exact_model)
    matched = set()
    database = Path(db_path).resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        counts['indexed_requests'], counts['indexed_without_thread'] = connection.execute(
            'SELECT count(*), count(*) FILTER (WHERE thread_id IS NULL) FROM requests').fetchone()
        counts['thread_candidates'] = counts['thread_filtered_out'] = 0
        if thread_id is not None:
            for rid, started, indexed_model, native_model in connection.execute(
                    'SELECT id, started, model, native_model FROM requests WHERE thread_id = ?', (thread_id,)):
                counts['thread_candidates'] += 1
                if ((model is not None and model not in (indexed_model, native_model)) or
                        (start is not None and (started is None or started < start)) or
                        (end is not None and (started is None or started > end))):
                    counts['thread_filtered_out'] += 1
                else:
                    matched.add(rid)
        counts['direct_indexed'] = sum(connection.execute('SELECT 1 FROM requests WHERE id = ?',
                                                          (rid,)).fetchone() is not None for rid in direct)
    ids = sorted(direct | matched)
    counts.update(direct_response_ids=len(direct), direct_unindexed=len(direct) - counts['direct_indexed'],
                  thread_matched_requests=len(matched), matched_requests=len(ids))
    return {'request_ids': ids, 'direct_response_ids': sorted(direct),
            'thread_request_ids': sorted(matched), 'thread_id': thread_id,
            'thread_source': threads.get(thread_id), 'counts': dict(counts)}
