"""External event timestamps; the agent receives no messages from this logger."""
import datetime as dt
from contextlib import ExitStack
import errno
import json
import os
import time


STORAGE_ERRORS = {errno.EDQUOT, errno.ENOSPC, errno.EROFS, errno.EIO}


def _storage_wait(error, state):
    if error.errno not in STORAGE_ERRORS:
        raise error
    state.update(status='waiting_for_storage', last_errno=error.errno,
                 last_error=str(error), storage_waits=state['storage_waits'] + 1)
    time.sleep(1)


def _open_output(path, state):
    while True:
        try:
            return path.open('a+b', buffering=0)
        except OSError as error:
            _storage_wait(error, state)


def _write_all(target, data):
    remaining = memoryview(data)
    while remaining:
        written = target.write(remaining)
        if not written:
            raise OSError(errno.EIO, 'Event stream write made no progress')
        remaining = remaining[written:]


def capture_stream(stream, raw_path, events_path, state=None):
    """Keep the current event until both copies are durably written exactly once."""
    state = state if state is not None else {}
    state.update(schema='pixelworld-event-capture-v1', status='starting', storage_waits=0)
    try:
        with ExitStack() as files:
            raw = files.enter_context(_open_output(raw_path, state))
            annotated = files.enter_context(_open_output(events_path, state))
            state['status'] = 'capturing'
            for line in iter(stream.readline, b''):
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError('non-object event')
                    event['_harness_received'] = {
                        'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'monotonic_ns': time.monotonic_ns(),
                    }
                    received = (json.dumps(event, ensure_ascii=False) + '\n').encode()
                except (ValueError, UnicodeError):
                    received = line
                raw_offset, annotated_offset = raw.tell(), annotated.tell()
                rollback = False
                while True:
                    try:
                        # A failed write can leave a partial line in either file.
                        # Roll back only this event; keep every earlier event.
                        if rollback:
                            raw.seek(raw_offset); raw.truncate(raw_offset)
                            annotated.seek(annotated_offset); annotated.truncate(annotated_offset)
                        _write_all(raw, line)
                        _write_all(annotated, received)
                        os.fsync(raw.fileno()); os.fsync(annotated.fileno())
                        state['status'] = 'capturing'
                        break
                    except OSError as error:
                        rollback = True
                        _storage_wait(error, state)
        state['status'] = 'complete'
    except BaseException as error:
        state.update(status='failed', last_error=str(error), last_errno=getattr(error, 'errno', None))
        raise
