"""Publish existing Flash videos and sanitized full traces without model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from tools.live_benchmark_data import case_data
from tools.prepare_hf_delivery import sanitize
from tools.publish_hf_incremental import package_case
from pixelworld.util import atomic_json, utc_now


def publish(cohort, api, limit=2):
    from huggingface_hub import CommitOperationAdd
    root=cohort/'example_publication'
    source=root/'publication/pixelworld';source.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((cohort/'experiment_manifest.json').read_text())
    arms=[a for a in manifest['arms'] if a!='replay']
    atomic_json(root/'live_sources.json',{'sources':[
        {'key':'flash_confirm_'+arm,'runs_root':str((cohort/'generations'/arm).resolve())}
        for arm in arms]})
    ledger=root/'ledger.json';state=json.loads(ledger.read_text()) if ledger.exists() else {}
    examples=state.setdefault('examples',{})
    jobs=[]
    for arm in arms:
        for run in (cohort/'generations'/arm).glob('*/*/case_*'):
            path=run/'run_manifest.json'
            if not path.is_file():continue
            record=json.loads(path.read_text())
            if record.get('status')=='running':continue
            relative=f"flash_confirm_{arm}/{record['model_key']}/{record['track']}/{run.name}.json"
            stamp=hashlib.sha256(path.read_bytes()).hexdigest()
            if examples.get(relative,{}).get('source_sha256')==stamp:continue
            jobs.append((record['case_id']!='0047',arm,run,relative,stamp))
    for _,arm,run,relative,stamp in sorted(jobs)[:limit]:
        target=source/relative
        previous=json.loads(target.read_text()) if target.exists() else None
        detail=case_data(run,source,previous)
        atomic_json(target,detail)
        package=package_case(source,relative)
        if package is None:continue
        public=json.loads(package['detail'])
        operations=[CommitOperationAdd(path_in_repo=name,path_or_fileobj=path)
                    for name,path in package['media'].items()]
        operations.append(CommitOperationAdd(path_in_repo='live/'+relative,path_or_fileobj=package['detail']))
        row={'arm':arm,'case':public['case_id'],'status':public['delivery_status'],
             'protocol_pass':bool(public.get('validation',{}).get('pass')),
             'source_sha256':stamp,'trace':public.get('trace_archive'),
             'detail':'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/live/'+relative,
             'candidates':[{k:c.get(k) for k in ('label','preview_mp4','poster')}
                           for c in public.get('candidates',[])]}
        updated={**examples,relative:row}
        report=sanitize({'updated_utc':utc_now(),'scope':'Existing Flash development videos; protocol failures remain labeled',
                         'examples':list(updated.values())})
        operations.append(CommitOperationAdd(path_in_repo='analyses/flash/examples.json',
                          path_or_fileobj=json.dumps(report,ensure_ascii=False).encode()))
        result=api.create_commit('HuggingFriends/PixelWorld',repo_type='dataset',operations=operations,
                                commit_message='Publish existing Flash candidate videos and full sanitized traces')
        examples[relative]=row;state['last_commit']=result.oid;atomic_json(ledger,state)
        print(arm,public['case_id'],public['delivery_status'],result.oid,flush=True)
    return len(examples)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--interval',type=float,default=120)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    from huggingface_hub import HfApi
    api=HfApi(token=args.token_file.read_text().strip())
    while True:
        try:print('Published examples',publish(args.cohort.resolve(),api),flush=True)
        except Exception as error:
            print(type(error).__name__,str(error),flush=True)
            if args.once:raise
        if args.once:break
        time.sleep(args.interval)
