"""Resume public result uploads while reserving HF API capacity for progress updates."""
import argparse
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

import requests


class ApiGovernor:
    def __init__(self, requests_per_second=1.5):
        self.interval = 1 / requests_per_second
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.cooldown_until = 0.0

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                ready = max(self.next_request, self.cooldown_until)
                if ready <= now:
                    self.next_request = now + self.interval
                    return
                delay = ready - now
            time.sleep(min(delay, 5))

    def reject(self, retry_after=None):
        try:
            seconds = max(1, float(retry_after))
        except (TypeError, ValueError):
            seconds = 300
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)
        print(f'HF API quota wait: {seconds:g} seconds; inference is independent', flush=True)


class LimitedSession(requests.Session):
    def __init__(self, governor):
        super().__init__()
        self.governor = governor

    def send(self, request, **kwargs):
        api_request = urlparse(request.url).hostname == 'huggingface.co'
        if api_request:
            self.governor.wait()
        response = super().send(request, **kwargs)
        if api_request and response.status_code == 429:
            self.governor.reject(response.headers.get('Retry-After'))
        return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--api-requests-per-second', type=float, default=1.5)
    args = parser.parse_args()
    if args.api_requests_per_second <= 0:
        raise ValueError('API request rate must be positive')
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    from huggingface_hub import HfApi, configure_http_backend
    governor = ApiGovernor(args.api_requests_per_second)
    configure_http_backend(backend_factory=lambda: LimitedSession(governor))
    api = HfApi(token=args.token_file.read_text().strip())
    while True:
        try:
            api.upload_large_folder('HuggingFriends/PixelWorld', folder_path=args.folder,
                repo_type='dataset', num_workers=args.workers)
            break
        except Exception as error:
            response = getattr(error, 'response', None)
            status = getattr(response, 'status_code', None)
            if status not in (429, 502, 503, 504):
                raise
            governor.reject(response.headers.get('Retry-After') if response is not None else None)
            print(f'Resumable HF upload startup/retry deferred after HTTP {status}', flush=True)
            governor.wait()
