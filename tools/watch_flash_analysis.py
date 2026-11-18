"""Publish current Flash reports while keeping scores outside the GitHub repo."""
import argparse
import json
from pathlib import Path
import time

from tools.analyze_flash_rollouts import build
from tools.hf_commit_budget import commit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-root', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--descriptions', type=Path, required=True)
    parser.add_argument('--web-root', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=120)
    args = parser.parse_args()
    from huggingface_hub import HfApi, CommitOperationAdd
    api = HfApi(token=args.token_file.read_text().strip())
    while True:
        try:
            operations = []
            for name, root,checkpoint in [('legacy',args.legacy_root,None),('confirmation',args.cohort,None),
                ('confirmation_native24',args.cohort,args.cohort/'quality/flash_quality_native24_checkpoint.json')]:
                output = root / 'analysis' / ('report_native24.json' if checkpoint else 'report.json')
                build(root, output, args.descriptions, root / 'analysis/trace_cache.json',checkpoint)
                raw = output.read_bytes()
                temporary = args.web_root / ('flash_' + name + '.tmp')
                temporary.write_bytes(raw)
                temporary.replace(args.web_root / ('flash_' + name + '.json'))
                operations.append(CommitOperationAdd(path_in_repo='analyses/flash/' + name + '.json', path_or_fileobj=raw))
            result = commit(api, repo_id='HuggingFriends/PixelWorld', repo_type='dataset',
                operations=operations, commit_message='Refresh trace-grounded Flash cost and quality reports')
            print(json.dumps({'updated_unix': time.time(), 'commit': result.oid}), flush=True)
        except Exception as error:
            print(type(error).__name__, str(error), flush=True)
        time.sleep(args.interval)
