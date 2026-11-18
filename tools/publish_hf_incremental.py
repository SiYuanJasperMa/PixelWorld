"""Publish closed case details and their playable media in one atomic HF commit."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import time

from tools.prepare_hf_delivery import public_case,export_trace_run,TRACE_FORMAT_VERSION

DATA_BASE = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/data/'


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def package_case(source, relative):
    path = (source / relative).resolve()
    if not path.is_relative_to(source.resolve()) or path.suffix != '.json':
        raise ValueError('Invalid public detail path')
    raw = path.read_bytes()
    original = json.loads(raw)
    if original.get('status') == 'running':
        return None
    if original.get('media_refresh_pending'):
        return None
    if original.get('delivery_status') == 'complete' and not original.get('candidates'):
        return None  # Native delivery is complete, but its playable preview is not ready.
    data = public_case(original)
    media = {}
    for candidate in data.get('candidates', []):
        for field in ('preview_mp4', 'poster'):
            name = candidate.get(field)
            if not name:
                continue
            asset = (source / name).resolve()
            if not asset.is_relative_to(source.resolve()) or not asset.is_file():
                return None
            if asset.suffix not in ('.png', '.jpg', '.mp4', '.webp'):
                raise ValueError('Unexpected media format')
            media['data/' + name] = asset
            candidate[field] = DATA_BASE + name
    data['publication'] = {'backend': 'huggingface', 'media_ready': True}
    bundle=source.parent.parent
    registry=read(bundle/'live_sources.json')
    suite=relative.split('/',1)[0]
    record=next((r for r in registry.get('sources',[]) if r['key']==suite),None)
    if record:
        from pixelworld.reuse import run_sources
        folder=Path(record['runs_root'])/original['model_key']/original['track']
        cache_key=Path(relative).stem
        run=next((run for run,key,_ in run_sources(folder,original['case_id']) if key==cache_key),None)
        if run:
            trace=bundle/'hf_incremental_traces'/Path(relative).with_suffix('.tar.gz')
            export_trace_run(run,trace)
            remote='live/'+str(Path(relative).with_suffix('.trace.tar.gz'))
            media[remote]=trace
            data['trace_archive']='https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/'+remote
            data['publication']['trace_ready']=True
            data['publication']['trace_format_version']=TRACE_FORMAT_VERSION
    return {'source_sha256': hashlib.sha256(raw).hexdigest(), 'media': media,
            'trace_ready':data.get('publication',{}).get('trace_ready',False),
            'trace_format_version':data.get('publication',{}).get('trace_format_version'),
            'detail': json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()}


def publish_cycle(source, state_path, api, batch_size=2):
    from huggingface_hub import CommitOperationAdd
    state = read(state_path)
    if batch_size<1:raise ValueError('Positive publication batch size required')
    ready = state.setdefault('ready', {})
    catalogue = read(source / 'catalogue.json')
    groups = catalogue.get('groups', [])
    if not groups:
        return state
    cursor = state.get('cursor', 0) % len(groups)
    selected = []
    delivery_priority=state.get('publication_round',0)%2==0
    checked = 0
    for group in groups[cursor:] + groups[:cursor]:
        checked += 1
        index = read(source / group['path'])
        histories = index.get('cases') or {}
        latest = (group.get('latest_completion') or ['', ''])[1]
        ids = ([latest] if latest in histories else []) + [k for k in sorted(histories, reverse=True) if k != latest]
        for case in ids:
            if delivery_priority:
                ordered=sorted(histories[case],key=lambda a:(a.get('status')!='complete',not a.get('reused',False),
                    a['attempt'] if a.get('status')=='complete' else -a['attempt']))
            else:
                ordered=sorted(histories[case],key=lambda a:-a['attempt'])
            for attempt in ordered:
                relative = attempt['detail']
                path = source / relative
                if not path.is_file() or attempt.get('status') == 'running':
                    continue
                raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                acknowledged=ready.get(relative,{})
                trace_required=(source.parent.parent/'live_sources.json').is_file()
                if acknowledged.get('source_sha256') == raw_hash and (not trace_required or
                        acknowledged.get('trace_ready') and acknowledged.get('trace_format_version',0)>=TRACE_FORMAT_VERSION):
                    continue
                package = package_case(source, relative)
                if package is None:
                    continue
                selected.append((relative, package))
                break
            if selected and selected[-1][0].startswith(str(Path(group['path']).parent) + '/'):
                break
        if len(selected) >= batch_size:
            break
    state['cursor'] = (cursor + checked) % len(groups)
    state['publication_round']=state.get('publication_round',0)+1
    if selected:
        assets = {}
        for _, package in selected:
            assets.update(package['media'])
        operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=path) for name, path in assets.items()]
        operations += [CommitOperationAdd(path_in_repo='live/' + relative, path_or_fileobj=package['detail'])
                       for relative, package in selected]
        from tools.hf_commit_budget import commit as dataset_commit
        commit = dataset_commit(api, repo_id='HuggingFriends/PixelWorld', repo_type='dataset',
            operations=operations, commit_message='Publish ready case videos and detailed traces together')
        now = time.time()
        for relative, package in selected:
            ready[relative] = {'source_sha256': package['source_sha256'],
                              'uploaded_unix': now, 'commit': commit.oid,
                              'media_files': len(package['media']), 'trace_ready':package['trace_ready'],
                              'trace_format_version':package.get('trace_format_version')}
        state['last_commit'] = commit.oid
        state['last_uploaded_unix'] = now
    state['checked_unix'] = time.time()
    temporary = state_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(state_path)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--batch-size',type=int,default=2)
    args = parser.parse_args()
    import os
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    from huggingface_hub import HfApi
    api = HfApi(token=args.token_file.read_text().strip())
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result = publish_cycle(args.source, args.state, api,args.batch_size)
                print(json.dumps({'checked_unix': result.get('checked_unix'),
                                  'ready_details': len(result.get('ready', {})),
                                  'last_commit': result.get('last_commit')}), flush=True)
            except Exception as error:
                print(type(error).__name__, str(error), flush=True)
            time.sleep(args.interval)
