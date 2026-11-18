"""Refresh GitHub's small dashboard and HF progress without blocking local inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
from pathlib import Path
import re
import time

from tools.publish_dashboard_snapshot import snapshot, SHELLS, validate_script_syntax
from tools.prepare_hf_delivery import sanitize
from tools.hf_commit_budget import commit as dataset_commit
from tools.prepare_hf_delivery import TRACE_FORMAT_VERSION


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
        current_traces=0
        for history in payload.get('cases',{}).values():
            for attempt in history:
                ready=ledger.get(attempt['detail'])
                current=attempt.get('detail_sha256')
                attempt['hf_detail_ready']=bool(ready and (not current or current==ready['source_sha256']))
                attempt['hf_trace_ready']=bool(attempt['hf_detail_ready'] and ready.get('trace_ready'))
                attempt['hf_trace_format_version']=ready.get('trace_format_version') if ready else None
                attempt['hf_trace_current_format']=bool(attempt['hf_trace_ready'] and
                    ready.get('trace_format_version',0)>=TRACE_FORMAT_VERSION)
                current_traces+=attempt['hf_trace_current_format']
                synced+=attempt['hf_detail_ready']
        payload['publication']={'hf_synced_details':synced,'hf_current_format_traces':current_traces,
                                'required_trace_format':TRACE_FORMAT_VERSION}
        group['publication']=payload['publication']
        items.append(('live/' + group['path'],payload))
    items += [('live/catalogue.json', catalogue), ('metadata/catalogue.json', catalogue),
              ('live/experiment_plan.json',sanitize(json.loads((source/'experiment_plan.json').read_text())))]
    evaluation_source=bundle/'publication/pixelworld/evaluation.json'
    if not evaluation_source.is_file():evaluation_source=source/'evaluation.json'
    if evaluation_source.is_file():
        items.append(('live/evaluation.json',sanitize(json.loads(evaluation_source.read_text()))))
    operations = [CommitOperationAdd(path_in_repo=name,
        path_or_fileobj=json.dumps(data, ensure_ascii=False).encode()) for name, data in items]
    commit = dataset_commit(api, repo_id='HuggingFriends/PixelWorld', repo_type='dataset',
        operations=operations, commit_message='Refresh live model and benchmark progress')
    return {'updated_utc': catalogue['updated_utc'], 'commit': commit.oid}


def hf_frontend(source, token_file):
    """Publish changed frontend assets independently of progress and videos."""
    from huggingface_hub import HfApi, CommitOperationAdd
    live_base='https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/'
    files={name:(source/name).read_text() for name in SHELLS}
    validate_script_syntax(files)
    for name, content in files.items():
        if re.search(r'/mnt/|/home/tiger/|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}',content):
            raise ValueError('Private data in frontend asset '+name)
        if name.endswith('.html'):
            content=re.sub(r'\sdata-live-base="[^"]*"','',content,count=1)
            files[name]=re.sub(r'<body\b','<body data-live-base="'+live_base+'"',content,count=1)
    digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    state=source.parent.parent/'state/hf_frontend.json'
    previous=json.loads(state.read_text()) if state.exists() else {}
    if previous.get('digest')==digest:return previous
    api=HfApi(token=token_file.read_text().strip() if token_file else None)
    result=api.create_commit('HuggingFriends/PixelWorld-dashboard',repo_type='space',
        operations=[CommitOperationAdd(path_in_repo=name,path_or_fileobj=text.encode())
                    for name,text in files.items()],commit_message='Refresh PixelWorld monitoring frontend')
    record={'digest':digest,'commit':result.oid,'updated_unix':time.time()}
    temporary=state.with_suffix('.tmp');temporary.write_text(json.dumps(record));temporary.replace(state)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--frontend-source', type=Path, help='Static page source when live data is exported separately')
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--interval', type=int, default=120)
    parser.add_argument('--hf-only', action='store_true', help='Publish results to HF while keeping generated data out of the GitHub code repository')
    args = parser.parse_args()
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}
            statuses = {}
            while True:
                for name, function, values in (
                    ('github', snapshot, (args.source, args.checkout,
                        'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/')),
                    ('hf', hf_progress, (args.source, args.token_file)),
                    ('hf_frontend', hf_frontend, (args.frontend_source or args.source, args.token_file))):
                    if name=='github' and args.hf_only:
                        statuses[name]={'enabled':False,'reason':'Generated metadata/media are published to HF'}
                        continue
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
