"""Refresh GitHub's small dashboard and HF progress without blocking local inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import time

from tools.publish_dashboard_snapshot import snapshot
from tools.prepare_hf_delivery import sanitize


def hf_progress(source, token_file):
    from huggingface_hub import HfApi, CommitOperationAdd
    token = token_file.read_text().strip() if token_file else None
    api = HfApi(token=token)
    catalogue = sanitize(json.loads((source / 'catalogue.json').read_text()))
    from urllib.parse import urljoin
    bundle=source.parent.parent
    monitor=bundle/'state/temporary_monitor_url.txt'
    if monitor.is_file():
        origin=urljoin(monitor.read_text().strip(),'.')
        if origin.startswith('https://'):
            catalogue['live_origin']=origin
    catalogue['detail_base']='https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/'
    ledger_path=bundle/'state/hf_incremental.json'
    ledger=json.loads(ledger_path.read_text()).get('ready',{}) if ledger_path.is_file() else {}
    items=[]
    for group in catalogue['groups']:
        payload=sanitize(json.loads((source / group['path']).read_text()))
        synced=0
        for history in payload.get('cases',{}).values():
            for attempt in history:
                ready=ledger.get(attempt['detail'])
                current=attempt.get('detail_sha256')
                attempt['hf_detail_ready']=bool(ready and (not current or current==ready['source_sha256']))
                attempt['hf_trace_ready']=bool(attempt['hf_detail_ready'] and ready.get('trace_ready'))
                synced+=attempt['hf_detail_ready']
        payload['publication']={'hf_synced_details':synced}
        group['publication']=payload['publication']
        items.append(('live/' + group['path'],payload))
    items += [('live/catalogue.json', catalogue), ('metadata/catalogue.json', catalogue),
              ('live/experiment_plan.json',sanitize(json.loads((source/'experiment_plan.json').read_text())))]
    operations = [CommitOperationAdd(path_in_repo=name,
        path_or_fileobj=json.dumps(data, ensure_ascii=False).encode()) for name, data in items]
    commit = api.create_commit('HuggingFriends/PixelWorld', repo_type='dataset',
        operations=operations, commit_message='Refresh live model and benchmark progress')
    return {'updated_utc': catalogue['updated_utc'], 'commit': commit.oid}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--interval', type=int, default=120)
    args = parser.parse_args()
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            statuses = {}
            while True:
                for name, function, values in (
                    ('github', snapshot, (args.source, args.checkout,
                        'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/')),
                    ('hf', hf_progress, (args.source, args.token_file))):
                    future = futures.get(name)
                    if future is not None and future.done():
                        try:
                            statuses[name] = {'ok': True, **future.result()}
                        except Exception as exc:
                            statuses[name] = {'ok': False, 'error': str(exc)}
                        futures.pop(name)
                    if name not in futures:
                        futures[name] = pool.submit(function, *values)
                payload = {'checked_unix': time.time(), **statuses}
                temporary = args.state.with_suffix('.tmp')
                temporary.write_text(json.dumps(payload, indent=2))
                temporary.replace(args.state)
                print(json.dumps(payload), flush=True)
                time.sleep(args.interval)
