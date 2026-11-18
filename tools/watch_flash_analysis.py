"""Publish current Flash reports while keeping scores outside the GitHub repo."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from tools.analyze_flash_rollouts import build
from tools.hf_commit_budget import commit


def publish_reports(api, reports):
    from huggingface_hub import CommitOperationAdd
    # Read the latest atomically written reports after the shared commit slot
    # opens. HF cooldown must not freeze local analysis or publish an old
    # report captured at the start of a long retry window.
    def operations():
        return [CommitOperationAdd(path_in_repo='analyses/flash/' + name + '.json',
                                   path_or_fileobj=path.read_bytes())
                for name, path in reports]
    return commit(api, repo_id='HuggingFriends/PixelWorld', repo_type='dataset',
                  operations_factory=operations,
                  commit_message='Refresh trace-grounded Flash cost and quality reports')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-root', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--descriptions', type=Path, required=True)
    parser.add_argument('--web-root', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=120)
    args = parser.parse_args()
    from huggingface_hub import HfApi
    api = HfApi(token=args.token_file.read_text().strip())
    pool = ThreadPoolExecutor(max_workers=1)
    publication = None
    while True:
        try:
            reports = []
            for name, root,checkpoint in [('legacy',args.legacy_root,None),('confirmation',args.cohort,None),
                ('confirmation_native24',args.cohort,args.cohort/'quality/flash_quality_native24_checkpoint.json')]:
                output = root / 'analysis' / ('report_native24.json' if checkpoint else 'report.json')
                build(root, output, args.descriptions, root / 'analysis/trace_cache.json',checkpoint)
                raw = output.read_bytes()
                temporary = args.web_root / ('flash_' + name + '.tmp')
                temporary.write_bytes(raw)
                temporary.replace(args.web_root / ('flash_' + name + '.json'))
                reports.append((name, output))
            if publication is not None and publication.done():
                try:
                    result = publication.result()
                    print(json.dumps({'published_unix': time.time(), 'commit': result.oid}), flush=True)
                except Exception as error:
                    print(type(error).__name__, str(error), flush=True)
                publication = None
            if publication is None:
                publication = pool.submit(publish_reports, api, reports)
            print(json.dumps({'local_reports_updated_unix': time.time(),
                              'publication_pending': not publication.done()}), flush=True)
        except Exception as error:
            print(type(error).__name__, str(error), flush=True)
        time.sleep(args.interval)
