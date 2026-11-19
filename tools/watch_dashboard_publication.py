"""Refresh GitHub's small dashboard and HF progress without blocking local inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

from tools.publish_dashboard_snapshot import snapshot, SHELLS, validate_script_syntax
from tools.prepare_hf_delivery import sanitize
from tools.hf_commit_budget import commit as dataset_commit
from tools.prepare_hf_delivery import TRACE_FORMAT_VERSION

def chart_evidence(source):
    """Only completed scores and their usage coverage trigger chart rebuilding."""
    from tools.baseline_data import video_baselines
    from tools.live_evaluation_data import read
    evaluation=read(source/'evaluation.json')
    main={'physics_iq_720p24fps','robojudge_main720p24fps',
          'pcve_standard720p24fps','phygenbench_t2v_main720p24fps'}
    cohorts=[{key:row.get(key) for key in ('suite','model_key','track','score')}
             for row in evaluation.get('cohorts',[]) if row.get('full_scope_scored')
             and row.get('suite') in main and row.get('track')=='vision-agent']
    keys={(row['suite'],row['model_key'],row['track']) for row in cohorts}
    usage=[]
    for group in read(source/'catalogue.json').get('groups',[]):
        if (group['suite'],group['model_key'],group['track']) in keys:
            usage.append({key:group.get(key) for key in ('suite','model_key','track',
                'mean_input_tokens','mean_output_tokens','mean_cached_input_tokens',
                'input_tokens_measured_cases','output_tokens_measured_cases',
                'cached_input_tokens_measured_cases')})
    renderer=Path(__file__).with_name('render_bench_scale_charts.py')
    payload={'cohorts':cohorts,'usage':usage,'baselines':video_baselines(source.parent.parent),
             'renderer_sha256':hashlib.sha256(renderer.read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def refresh_charts(source, frontend):
    """Build measured-cost charts off the progress publication thread."""
    bundle=source.parent.parent
    os.environ['MPLCONFIGDIR']=str(bundle/'state/matplotlib')
    from tools.cost_report import cohort_report, DEFAULT_PRICES
    from tools.render_bench_scale_charts import score_specs, render
    state=bundle/'state/benchmark_chart_refresh.json'
    digest=chart_evidence(source)
    previous=json.loads(state.read_text()) if state.is_file() else {}
    if previous.get('digest')==digest:return previous
    costs=cohort_report(bundle/'formal_generations',{},uniform_price=DEFAULT_PRICES['gpt61-sol'])
    report=bundle/'state/cost_report_accepted_current.json'
    temporary=report.with_suffix('.tmp');temporary.write_text(json.dumps(costs,indent=2));temporary.replace(report)
    stage=bundle/'state/chart_refresh_staging';stage.mkdir(parents=True,exist_ok=True)
    specs=score_specs(bundle,costs)
    for name,spec in specs.items():render(name,spec,stage/(name+'_scale'),costs)
    charts=frontend/'charts';charts.mkdir(exist_ok=True)
    for name in specs:
        for suffix in ('.svg','.png'):
            (stage/(name+'_scale'+suffix)).replace(charts/(name+'_scale'+suffix))
    record={'digest':digest,'updated_unix':time.time(),'charts':len(specs),
            'cost_scope':'accepted full cohorts; reported cache; shared reference tariff; retries excluded'}
    temporary=state.with_suffix('.tmp');temporary.write_text(json.dumps(record));temporary.replace(state)
    return record


def hf_progress(source, token_file):
    from huggingface_hub import HfApi, CommitOperationAdd
    token = token_file.read_text().strip() if token_file else None
    api = HfApi(token=token)
    started=time.monotonic()
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
    # The live exporter owns progress; merge baseline evidence from its bundle
    # rather than relying on a static frontend copy of experiment_plan.json.
    from tools.baseline_data import video_baselines
    plan = json.loads((source/'experiment_plan.json').read_text())
    plan['video_baselines'] = video_baselines(bundle)
    items += [('live/catalogue.json', catalogue), ('metadata/catalogue.json', catalogue),
              ('live/experiment_plan.json',sanitize(plan))]
    evaluation_source=bundle/'publication/pixelworld/evaluation.json'
    if not evaluation_source.is_file():evaluation_source=source/'evaluation.json'
    if evaluation_source.is_file():
        items.append(('live/evaluation.json',sanitize(json.loads(evaluation_source.read_text()))))
    analysis_source=source/'analysis_progress.json'
    if not analysis_source.is_file():
        analysis_source=bundle/'live_preview/pixelworld/analysis_progress.json'
    if analysis_source.is_file():
        items.append(('live/analysis_progress.json',sanitize(json.loads(analysis_source.read_text()))))
    # Completed groups are re-exported with fresh poll timestamps even when no
    # status, token usage, score or media readiness changed. Do not resend them.
    state_path=bundle/'state/hf_progress_ack.json'
    previous=json.loads(state_path.read_text()) if state_path.is_file() else {}
    acknowledged=previous.get('digests',{})
    operations=[];digests={};bytes_sent=0
    for name,data in items:
        digest=progress_digest(data);digests[name]=digest
        if acknowledged.get(name)==digest:continue
        encoded=json.dumps(data,ensure_ascii=False).encode();bytes_sent+=len(encoded)
        operations.append(CommitOperationAdd(path_in_repo=name,path_or_fileobj=encoded))
    read_seconds=time.monotonic()-started
    if operations:
        result=dataset_commit(api,repo_id='HuggingFriends/PixelWorld',repo_type='dataset',
            operations=operations,commit_message='Refresh changed live model and benchmark progress')
        previous={'digests':digests,'updated_utc':catalogue['updated_utc'],'commit':result.oid}
        state_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=state_path.with_suffix('.tmp');temporary.write_text(json.dumps(previous,indent=2));temporary.replace(state_path)
    return {'updated_utc':previous.get('updated_utc'), 'commit':previous.get('commit'),
            'changed_items':len(operations),'unchanged_items':len(items)-len(operations),
            'bytes_sent':bytes_sent,'read_seconds':round(read_seconds,3),
            'cycle_seconds':round(time.monotonic()-started,3)}


def progress_digest(value):
    """Ignore poll timestamps; preserve every actual result/readiness field."""
    def stable(item):
        if isinstance(item,dict):
            return {k:stable(v) for k,v in item.items() if k not in ('updated_utc','generation_updated_utc')}
        if isinstance(item,list):return [stable(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(stable(value),sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def hf_frontend(source, token_file):
    """Publish changed frontend assets independently of progress and videos."""
    from huggingface_hub import HfApi, CommitOperationAdd
    live_base='https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/'
    files={name:(source/name).read_text() for name in SHELLS}
    for name in ('phygen_firstframe_smoke.html', 'phygen_acd_smoke.html'):
        if (source/name).is_file():
            files[name]=(source/name).read_text()
    for path in (source/'charts').glob('*.svg'):
        files['charts/'+path.name]=path.read_text()
    binary_assets={ 'charts/'+path.name:path for path in (source/'charts').glob('*.png') }
    validate_script_syntax(files)
    for name, content in files.items():
        if re.search(r'/mnt/|/home/tiger/|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}',content):
            raise ValueError('Private data in frontend asset '+name)
        if name.endswith('.html'):
            content=re.sub(r'\sdata-live-base="[^"]*"','',content,count=1)
            files[name]=re.sub(r'<body\b','<body data-live-base="'+live_base+'"',content,count=1)
    assets={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in binary_assets.items()}
    digest=hashlib.sha256(json.dumps({'text':files,'binary':assets},sort_keys=True).encode()).hexdigest()
    state=source.parent.parent/'state/hf_frontend.json'
    previous=json.loads(state.read_text()) if state.exists() else {}
    if previous.get('digest')==digest:return previous
    api=HfApi(token=token_file.read_text().strip() if token_file else None)
    result=api.create_commit('HuggingFriends/PixelWorld-dashboard',repo_type='space',
        operations=[CommitOperationAdd(path_in_repo=name,path_or_fileobj=text.encode())
                    for name,text in files.items()]+[
                        CommitOperationAdd(path_in_repo=name,path_or_fileobj=str(path)) for name,path in binary_assets.items()],
        commit_message='Refresh PixelWorld monitoring frontend')
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
                    ('charts', refresh_charts, (args.source,args.frontend_source or args.source)),
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
