from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import errno
import time
from pathlib import Path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode('utf-8')
    atomic_bytes(path, data)


def atomic_bytes(path: Path, data: bytes) -> None:
    """Persist the same buffered bytes through an opt-in storage interruption."""
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open('wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            return
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if (os.environ.get('PIXELWORLD_PERSIST_STORAGE_WAIT') != '1'
                    or error.errno not in (errno.EDQUOT, errno.ENOSPC, errno.EROFS)):
                raise
            # Retry these exact record bytes, never an agent/model invocation.
            time.sleep(2)


def write_stream_bytes(handle, data: bytes) -> None:
    """Retry an unbuffered seekable log chunk without repeating a model call."""
    start = handle.tell()
    written = 0
    while written < len(data):
        try:
            handle.seek(start + written)
            count = handle.write(data[written:])
            if not count:
                raise OSError(errno.EIO, 'Log write made no progress')
            written += count
        except OSError as error:
            if (os.environ.get('PIXELWORLD_PERSIST_STORAGE_WAIT') != '1'
                    or error.errno not in (errno.EDQUOT, errno.ENOSPC, errno.EROFS)):
                raise
            time.sleep(2)


def fsync_stream(handle) -> None:
    while True:
        try:
            handle.flush()
            os.fsync(handle.fileno())
            return
        except OSError as error:
            if (os.environ.get('PIXELWORLD_PERSIST_STORAGE_WAIT') != '1'
                    or error.errno not in (errno.EDQUOT, errno.ENOSPC, errno.EROFS)):
                raise
            time.sleep(2)


def slug(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
