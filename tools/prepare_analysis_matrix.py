"""Prepare selected-model analyses while preserving existing configs and runs."""
from pathlib import Path
import argparse
import json
import sys

CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE))
from pixelworld.config import load_config
from pixelworld.media import contract

MODELS={'gpt61-sol','gpt56-sol','gpt56-terra','gpt56-luna','deepseek-v41'}

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():return
    path.write_text(json.dumps(data,indent=2)+'\n')

def prepare(root,model_keys=None):
    selected=set(model_keys or MODELS)
    bundle=json.loads((root/'bundle.json').read_text());items=[]
    for suite,paths in bundle['configs'].items():
        base=json.loads((root/paths['full']).read_text())
        models=[{**m,'tracks':['text']}for m in base['models']if m['key']in selected]
        cfg={**base,'models':models,'protocol':base['protocol']+'-text-analysis',
             'runs_root':str(root/'analysis_generations'/(suite+'_text'))}
        path=root/'configs'/(suite+'_text_analysis.json');write(path,cfg);actual=load_config(path)
        items.append({'condition':suite+'_text','config':str(path),'cases':len(actual.case_ids)*len(actual.models),'stage':'prepared_or_existing_config_preserved'})
    base=json.loads((root/bundle['configs']['physics_iq']['full']).read_text())
    specs=[('res480',854,480,24),('res1080',1920,1080,24),
           ('fps12',1280,720,12),('fps48',1280,720,48)]
    for label,w,h,fps in specs:
        target=root/'analysis_inputs'/('physics_iq_'+label)
        for cid in base['case_ids']:
            source=Path(base['inputs_root'])/cid;dest=target/cid;dest.mkdir(parents=True,exist_ok=True)
            for name in ['first_frame.png','prompt.txt']:
                path=dest/name
                if not path.exists():path.symlink_to((source/name).resolve())
            spec=contract(source)|{'width':w,'height':h,'fps':fps,'frames':5*fps}
            if (w,h)!=(1280,720):spec['conditioning_resize_filter']='lanczos'
            write(dest/'task_spec.json',spec);contract(dest)
        models=[{**m,'tracks':['vision-agent']}for m in base['models']if m['key']in selected]
        cfg={**base,'models':models,'protocol':base['protocol']+'-'+label+'-analysis',
             'inputs_root':str(target),'runs_root':str(root/'analysis_generations'/('physics_iq_'+label))}
        path=root/'configs'/('physics_iq_'+label+'_analysis.json');write(path,cfg);actual=load_config(path)
        items.append({'condition':'physics_iq_'+label,'config':str(path),'cases':len(actual.case_ids)*len(actual.models),'stage':'prepared_or_existing_config_preserved'})
    for label in ('low','higher'):
        path=root/'configs'/f'physics_iq_thinking_{label}_analysis.json'
        if path.exists():
            cfg=load_config(path)
            items.append({'condition':'physics_iq_thinking_'+label,'config':str(path),
                          'cases':len(cfg.case_ids)*len(cfg.models),'stage':'existing_config_preserved'})
    result={'models':sorted(selected),'shared_main':'Vision 720p/24FPS/High; reuse once',
            'prepared':items,'prepared_cases':sum(x['cases']for x in items),
            'pending':[{'condition':'supported lower/higher effort','cases_max':198*len(selected)*2,'reason':'verify native provider acceptance and preserve its evidence'},
                       {'condition':'PhyGenBench AgentPixel→Qwen→I2V Text/Vision','cases':160*len(selected)*2,'reason':'complete and validate model-specific initial-frame preparation'}],
            'generation_started':False,'main_priority':True}
    (root/'analysis_bundle.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--models',nargs='+')
    args=p.parse_args();r=prepare(args.bundle.resolve(),args.models);print(json.dumps({'prepared_configs':len(r['prepared']),'prepared_cases':r['prepared_cases'],'generation_started':False}))
