"""Prepare, inspect and explicitly launch a reviewable PixelWorld experiment bundle.

This is the common entrypoint for collaborators. Preparation never starts a
model. Full start requires a separate explicit review flag and readiness gates.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE))
from pixelworld.media import contract, task_rules
from pixelworld.config import load_config

SUITES={
 'physics_iq':('physics_iq_720p24fps_full.json','0001'),
 'robojudge':('robojudge_main720p24fps_full.json','common50_0000'),
 'pcve':('pcve_standard720p24fps_full.json','pcve_0000'),
 'phygenbench':('phygenbench_t2v_main720p24fps_full.json','phygen_001')}

def read(path):return json.loads(path.read_text())
def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)
def sha(path):
 h=hashlib.sha256()
 with path.open('rb')as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()

def prepare(args):
 root=args.bundle.resolve();legacy=args.legacy_root.resolve();roster=read(args.roster)
 if root.exists()and(root/'bundle.json').exists():raise FileExistsError('Bundle exists; preserve it or choose a new path')
 configs={};counts={}
 for suite,(old_file,smoke_case)in SUITES.items():
  old=read(legacy/old_file);inputs=Path(old['inputs_root'])
  ids=sorted(p.name for p in inputs.iterdir()if(p/'prompt.txt').exists())
  counts[suite]=len(ids)
  if suite=='phygenbench':
   # The corrected T2V condition explicitly requests image-space photographic
   # generation. Pre-boundary T2V results remain legacy, never silently reused.
   target=root/'inputs'/suite
   for cid in ids:
    dst=target/cid;dst.mkdir(parents=True,exist_ok=True)
    shutil.copy2(inputs/cid/'prompt.txt',dst/'prompt.txt')
    spec=contract(inputs/cid)|{'visual_target':'photoreal'}
    write(dst/'task_spec.json',spec)
   inputs=target
  cfg={'protocol':old['protocol']+('-photoreal-v2'if suite=='phygenbench'else''),
       'inputs_root':str(inputs),'runs_root':str(root/'formal_generations'/suite),
       'case_ids':ids,'max_concurrency':4,'max_per_config':1,
       'max_attempts':1,'max_total_attempts':roster['max_total_model_attempts'],
       'output_token_budget':roster['output_token_budget'],'models':[]}
  cfg['filesystem_policy']=roster.get('filesystem_policy','case-only-v1')
  for model in roster['models']:
   m={k:model[k]for k in ['key','model','reasoning_effort']}
   m.update(provider=model.get('provider',roster['provider']),cli_backend='codex',tracks=['vision-agent'])
   cfg['models'].append(m)
  write(root/'configs'/f'{suite}_full.json',cfg)
  smoke=json.loads(json.dumps(cfg));smoke.update(case_ids=[smoke_case],runs_root=str(root/'smoke_generations'/suite),max_concurrency=2)
  for model,source in zip(smoke['models'],roster['models']):
   if source.get('analysis'):model['tracks']=['vision-agent','text']
  write(root/'configs'/f'{suite}_smoke.json',smoke)
  for mode in ['text','vision-agent']:
   source=inputs/smoke_case;spec=contract(source)
   d=root/'prompt_review'/suite/mode;d.mkdir(parents=True,exist_ok=True)
   shutil.copy2(source/'prompt.txt',d/'prompt.txt');write(d/'task_spec.json',spec)
   (d/'GENERATION_RULES.md').write_text(task_rules(spec))
   # Use the actual task builder, including T2V and editing modality boundaries.
   from pixelworld.runner import generation_task_text
   text=generation_task_text(mode,spec)
   (d/'TASK.md').write_text(text)
  configs[suite]={'full':f'configs/{suite}_full.json','smoke':f'configs/{suite}_smoke.json'}
 write(root/'READINESS.json',{'approved':False,'full_started':False,'gates':{
   'runtime':False,'real_generation_smokes':False,'official_evaluator_smokes':False,
   'reuse_audit':False,'dashboard':False,'attempt_and_usage_tests':False}})
 write(root/'limits_smoke.json',{'max_per_model':1,'max_total_runs':3,
       'max_runs_by_config':{f'{s}_smoke.json':1 for s in SUITES},'prioritize_less_attempted_cases':True,
       'min_cgroup_memory_headroom_bytes':12*1024**3})
 write(root/'limits_full.json',{'max_per_model':2,'max_total_runs':8,
       'max_runs_by_config':{f'{s}_full.json':4 for s in SUITES},'prioritize_less_attempted_cases':True,
       'min_cgroup_memory_headroom_bytes':16*1024**3})
 write(root/'bundle.json',{'version':'pixelworld-review-bundle-v1','models':roster['models'],
      'cases':counts,'configs':configs,'main_track':'vision-agent','codex_version':roster['codex_version'],
      'legacy_root':str(legacy),'full_launch_requires_explicit_review':True})
 print(json.dumps({'prepared':str(root),'cases':counts,'inference_started':False},indent=2))

def start(args):
 root=args.bundle.resolve();bundle=read(root/'bundle.json');ready=read(root/'READINESS.json')
 scope='full'if args.full else'smoke'
 if args.full:
  if not args.review_approved and not ready.get('approved'):raise ValueError('Full launch requires --review-approved after the user reviews this bundle')
  gates=ready['gates']
  if getattr(args,'generation_only',False):
   missing=[name for name in ('generation_runtime','filesystem_isolation','attempt_and_usage_tests')
            if gates.get(name) is not True]
  else:
   missing=[name for name,value in gates.items()if value is not True]
  if missing:raise ValueError('Readiness gates incomplete: '+', '.join(missing))
 limits=read(root/f'limits_{scope}.json')
 write(root/'scheduling_limits.json',limits);write(root/'configs/scheduling_limits.json',limits)
 manager_map={}
 for suite,paths in bundle['configs'].items():
  path=root/paths[scope];config=load_config(path)
  if scope=='smoke'and len(config.case_ids or())!=1:raise ValueError('Smoke must have exactly one selected case')
  state=root/'state'/f'{suite}_{scope}.json';state.parent.mkdir(parents=True,exist_ok=True)
  manager_map[str(path)]={'pid_file':str(state.with_suffix('.pid')),
                          'state_file':str(state),'code_root':str(CODE)}
  launch=state.with_suffix('.launcher.json')
  if launch.exists():
   prior=read(launch);proc=Path('/proc')/str(prior.get('pid'))/'cmdline'
   if proc.exists() and str(path).encode() in proc.read_bytes() and b'manage_matrix.py' in proc.read_bytes():
    continue
  command=[sys.executable,'-u',str(CODE/'tools/manage_matrix.py'),'--config',str(path),
           '--code-root',str(CODE),'--state',str(state),'--resume']
  with state.with_suffix('.log').open('ab')as log:
   p=subprocess.Popen(command,cwd=CODE,env=os.environ.copy(),stdin=subprocess.DEVNULL,
                      stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  write(state.with_suffix('.launcher.json'),{'pid':p.pid,'scope':scope,'config':str(path)})
  state.with_suffix('.pid').write_text(str(p.pid))
 write(root/f'{scope}_manager_map.json',manager_map)
 retry_state=root/'state'/f'{scope}_retries.json'
 prior_pid=retry_state.with_suffix('.pid')
 active=False
 if prior_pid.exists():
  proc=Path('/proc')/prior_pid.read_text().strip()/'cmdline'
  active=proc.exists()and b'watch_failed_retries.py'in proc.read_bytes()
 if not active:
  retry=[sys.executable,'-u',str(CODE/'tools/watch_failed_retries.py')]
  for paths in bundle['configs'].values():retry+=['--config',str(root/paths[scope])]
  retry+=['--extra-attempts','1','--max-total-attempts','5','--continuous','--cooldown','300',
          '--manager-map',str(root/f'{scope}_manager_map.json'),'--apply','--watch','--interval','60',
          '--status',str(retry_state)]
  with retry_state.with_suffix('.log').open('ab')as log:
   p=subprocess.Popen(retry,cwd=CODE,env=os.environ.copy(),stdin=subprocess.DEVNULL,
                      stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  prior_pid.write_text(str(p.pid))
 if args.full:
  ready.update(approved=True,full_started=True,
      launch_scope='generation_only'if getattr(args,'generation_only',False)else'full_pipeline')
  write(root/'READINESS.json',ready)
 print('Started '+scope+' managers; logs/state retained in bundle.')

def pause(args):
 root=args.bundle.resolve();bundle=read(root/'bundle.json');scope='full'if args.full else'smoke'
 for suite,paths in bundle['configs'].items():
  cfg=load_config(root/paths[scope]);cfg.runs_root.mkdir(parents=True,exist_ok=True)
  (cfg.runs_root/'STOP').touch()
  state=root/'state'/f'{suite}_{scope}.json';state.parent.mkdir(parents=True,exist_ok=True)
  state.with_name(state.stem+'.STOP').touch()
 print('Paused new dispatch; active runs may finish and their traces are preserved.')

def status(args):
 root=args.bundle.resolve();bundle=read(root/'bundle.json')
 result={'readiness':read(root/'READINESS.json'),'groups':{}}
 for suite,paths in bundle['configs'].items():
  cfg=load_config(root/paths['full'if args.full else'smoke'])
  p=cfg.runs_root/'status.json'
  result['groups'][suite]=read(p)if p.exists()else{'phase':'not_started'}
 print(json.dumps(result,indent=2))

def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
 prep=sub.add_parser('prepare');prep.add_argument('--input-config-root','--legacy-root',dest='legacy_root',type=Path,required=True,
      help='Root containing the frozen benchmark adapter configs; --legacy-root remains a compatibility alias')
 prep.add_argument('--bundle',type=Path,required=True);prep.add_argument('--roster',type=Path,default=CODE/'configs/final-models.example.json')
 for action in ['start','status','pause','resume']:
  q=sub.add_parser(action);q.add_argument('--bundle',type=Path,required=True);q.add_argument('--full',action='store_true')
  if action in ['start','resume']:
   q.add_argument('--review-approved',action='store_true')
   q.add_argument('--generation-only',action='store_true',help='Start reviewed generation after runtime/budget gates; evaluation gates remain visible')
 args=p.parse_args();{'prepare':prepare,'start':start,'resume':start,'status':status,'pause':pause}[args.action](args)

if __name__=='__main__':main()
