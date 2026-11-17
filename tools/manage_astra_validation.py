"""Observe the current Astra run, then validate one isolated route before full dispatch."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)


def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--code-root',type=Path,required=True)
    args=p.parse_args();root=args.bundle.resolve();state=root/'state/astra_validation.json';child=None
    while not(root/'state/ASTRA_VALIDATION_STOP').exists():
        manifests=[read(x)for x in(root/'formal_generations').glob('*/gpt6-astra/vision-agent/case_*/run_manifest.json')]
        smoke=root/'astra_validation_generations/physics_iq/gpt6-astra/vision-agent'
        records=manifests+[read(x)for x in smoke.glob('case_*/run_manifest.json')]
        success=next((x for x in records if x.get('status')=='complete'and x.get('validation',{}).get('pass')),None)
        if success:
            for name in ['scheduling_limits.json','configs/scheduling_limits.json','limits_full.json']:
                path=root/name;limits=read(path)
                for key in list(limits.get('paused_models_by_config',{})):
                    limits['paused_models_by_config'][key]=[m for m in limits['paused_models_by_config'][key]if m!='gpt6-astra']
                write(path,limits)
            write(state,{'phase':'validated','case_id':success.get('case_id'),'output_tokens':success.get('token_usage',{}).get('output_tokens'),'full_dispatch_restored':True,'max_concurrency':1,'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
            (root/'state/ASTRA_VALIDATED').touch()
            return
        live=[m for m in manifests if m.get('status')=='running']
        if live:
            write(state,{'phase':'observing_existing_run','cases':[m.get('case_id')for m in live],'current_run_preserved':True,'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
        else:
            config=root/'configs/astra_validation_smoke.json'
            if child is None or child.poll()is not None:
                log=root/'state/astra_validation_manager.log'
                with log.open('ab')as output:
                    child=subprocess.Popen([sys.executable,'-u',str(args.code_root/'tools/manage_matrix.py'),'--config',str(config),'--code-root',str(args.code_root),'--state',str(root/'state/astra_smoke_manager.json'),'--resume'],cwd=args.code_root,env=os.environ.copy(),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            write(state,{'phase':'isolated_smoke','manager_pid':child.pid,'case_id':'0001','max_concurrency':1,'same_main_prompt_and_media':True,'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
        time.sleep(30)


if __name__=='__main__':main()
