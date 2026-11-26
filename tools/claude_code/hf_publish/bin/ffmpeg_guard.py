#!/usr/bin/env python3
"""Drop-in ffmpeg for the PixelWorld exporter (PIXELWORLD_FFMPEG=<this file>).

tools.live_benchmark_data.export_native_preview encodes `<run>/.../frames/%02d.png`
as soon as all NN.png names exist.  For a running case (and for a snapshot round
that is still being written) a frame can be present but not yet fully written;
ffmpeg then fails with "[png] chunk too big / Invalid data found when processing
input", the CalledProcessError is not caught by the exporter (it only catches
OSError/ValueError/...), and the whole render job - including history backfill
batches of other cases - is dropped and retried next cycle.

This wrapper:
  * for a PNG-sequence input, structurally checks every frame (signature, chunk
    lengths, CRCs, IEND).  Incomplete frames are re-checked for up to
    FFMPEG_GUARD_WAIT seconds (default 60) so a frame still being written does not
    abort the export;
  * runs the real ffmpeg with the original arguments, unchanged (no pixels are
    altered or synthesized).  If frames are still incomplete after the wait, or
    ffmpeg reports a PNG decode error, exit non-zero instead of letting ffmpeg 4.4
    exit 0 with a silently SHORT preview (seen: Physics-IQ case_0022 candidate 1 =
    12/120 frames, case_0025 candidate 2 = 20/120);
  * on any failure appends one JSON line to FFMPEG_GUARD_LOG naming the input,
    the resolved path (exFAT offload or local) and the offending frames.
Never prints secrets; only file names/sizes.
"""
import json
import os
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

REAL = os.environ.get('FFMPEG_GUARD_REAL', '/usr/bin/ffmpeg')
LOG = os.environ.get('FFMPEG_GUARD_LOG')
WAIT = float(os.environ.get('FFMPEG_GUARD_WAIT', '60'))


def png_problem(path):
    try:
        data = path.read_bytes()
    except OSError as error:
        return f'read: {type(error).__name__}: {error.strerror}'
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return f'bad signature (size {len(data)})'
    i = 8
    while i + 8 <= len(data):
        n, kind = struct.unpack('>I4s', data[i:i + 8])
        if n > 0x7fffffff or i + 12 + n > len(data):
            return f'chunk too big: {kind!r} len={n} at {i}, file size {len(data)}'
        if zlib.crc32(data[i + 4:i + 8 + n]) & 0xffffffff != struct.unpack('>I', data[i + 8 + n:i + 12 + n])[0]:
            return f'crc mismatch in {kind!r} at {i}'
        if kind == b'IEND':
            return None
        i += 12 + n
    return f'truncated / no IEND (size {len(data)})'


def frames_of(argv):
    if '-i' not in argv:
        return None
    pattern = argv[argv.index('-i') + 1]
    if '%' not in pattern or not pattern.endswith('.png'):
        return None
    count = int(argv[argv.index('-frames:v') + 1]) if '-frames:v' in argv else None
    start = 0
    paths = []
    while count is None or len(paths) < count:
        path = Path(pattern % (start + len(paths)))
        if not path.exists():
            break
        paths.append(path)
    return pattern, paths


def log(record):
    if not LOG:
        return
    record['utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with open(LOG, 'a') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def main():
    argv = sys.argv[1:]
    found = frames_of(argv)
    bad, waited = {}, 0.0
    if found:
        pattern, paths = found
        deadline = time.monotonic() + WAIT
        while True:
            _, paths = frames_of(argv)
            bad = {p.name: e for p in paths if (e := png_problem(p))}
            want = int(argv[argv.index('-frames:v') + 1]) if '-frames:v' in argv else len(paths)
            if len(paths) < want:
                bad[pattern % len(paths)] = f'missing (only {len(paths)}/{want} frames present)'
            if not bad or time.monotonic() >= deadline:
                break
            time.sleep(3)
        waited = WAIT - max(0.0, deadline - time.monotonic())
        if bad:
            log({'event': 'invalid-frames', 'input': pattern,
                 'resolved_dir': str(Path(pattern).parent.resolve()),
                 'frames': len(paths), 'bad': dict(list(bad.items())[:10]), 'bad_count': len(bad),
                 'waited_s': round(waited, 1)})
        elif waited > 3.5:
            log({'event': 'recovered-after-wait', 'input': pattern, 'waited_s': round(waited, 1)})
    if bad:
        # ffmpeg 4.4 exits 0 after dropping undecodable frames, and the exporter then
        # caches a short preview forever (assets/<pixels digest>/preview.mp4 is never
        # rebuilt).  Fail instead: the exporter discards preview.tmp.mp4 and retries
        # the case on a later cycle, when the frames are complete.
        sys.stderr.write(f'ffmpeg_guard: {len(bad)} incomplete/invalid frame(s) in {pattern}; not encoding\n')
        sys.exit(65)
    process = subprocess.run([REAL, *argv], stderr=subprocess.PIPE)
    sys.stderr.buffer.write(process.stderr)
    decode_error = b'Invalid data found' in process.stderr or b'chunk too big' in process.stderr
    if process.returncode or decode_error:
        log({'event': 'ffmpeg-failed', 'rc': process.returncode,
             'input': found[0] if found else next((argv[i + 1] for i, a in enumerate(argv) if a == '-i'), None),
             'resolved_dir': str(Path(found[0]).parent.resolve()) if found else None,
             'bad_frames_precheck': bad_count if (bad_count := len(bad)) else 0,
             'stderr_tail': re.sub(r'0x[0-9a-f]+', '0x', process.stderr.decode(errors='replace'))[-400:]})
    sys.exit(process.returncode or (66 if decode_error and found else 0))


if __name__ == '__main__':
    main()
