"""Keep the Hugging Face dataset progress mirror current without touching the Space.

The full dashboard publication watcher also republishes the legacy frontend and
charts. This narrower watcher calls only `hf_progress`, which updates
`live/` progress JSON in `HuggingFriends/PixelWorld`.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import time

from tools.watch_dashboard_publication import hf_progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True,
                        help='Sanitized publication bundle (catalogue.json plus group payloads)')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=180)
    args = parser.parse_args()
    if args.interval < 30:
        parser.error('Use at least 30 seconds between dataset progress cycles')
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result = {'ok': True, **hf_progress(args.source, args.token_file)}
            except Exception as error:
                result = {'ok': False, 'error_type': type(error).__name__}
            payload = {'checked_unix': time.time(), **result}
            temporary = args.state.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, indent=2))
            temporary.replace(args.state)
            print(json.dumps(payload), flush=True)
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
