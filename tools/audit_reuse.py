"""Audit existing outputs before reuse; never resample, launch, or rewrite history."""
from __future__ import annotations
import argparse, collections, hashlib, json, sys
from pathlib import Path

CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE))
from pixelworld.config import load_config
from pixelworld.media import contract, task_rules
from pixelworld.retries import infrastructure_failure

def sha(path):
 h=hashlib.sha256()
 with path.open('rb')as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()

def model_matches(model, manifest, run):
 if manifest.get('model') == model.model:return True
 # Only accept explicit native identities from the run's recorded snapshot.
 expected=model.model.removeprefix('traex/')
 snapshot=run/'logs/native_model_snapshot.json'
 try:record=json.loads(snapshot.read_text())
 except (OSError,ValueError):return False
 return record.get('model')==expected and record.get('model_key')==model.key

def audit(config_path, legacy, compatible_history=False):
 cfg=load_config(config_path);eligible=[];excluded=collections.Counter();inputs={}
 for model in cfg.models:
  for track in model.tracks:
   parent=legacy/model.key/track
   if not parent.exists():continue
   grouped={}
   for p in parent.iterdir():
    if p.is_dir()and p.name.startswith('case_'):
     try:case=p.name[5:].rsplit('_attempt_',1)[0];attempt=int(p.name.rsplit('_',1)[-1])
     except ValueError:continue
     grouped.setdefault(case,[]).append((attempt,p))
   for cid,attempts in grouped.items():
    source=cfg.inputs_root/cid
    if not source.exists():continue
    if cid not in inputs:
     spec=contract(source)
     names=['first_frame.png','prompt.txt']
     if spec['task_type']=='video_edit':
      names+=['source_video.mp4','source_pixels.npz','source_info.json','source_inspection.png']
     if spec['task_type']=='v2v':
      names+=['conditioning.mp4','conditioning_info.json','conditioning_inspection.png']
     hashes={name:sha(source/name)for name in names if(source/name).is_file()}
     inputs[cid]=(spec,hashes,hashlib.sha256(task_rules(spec).encode()).hexdigest())
    spec,hashes,rules_hash=inputs[cid];input_hash=hashes.get('first_frame.png');prompt_hash=hashes['prompt.txt']
    model_attempts=0
    for number,run in sorted(attempts):
     try:m=json.loads((run/'run_manifest.json').read_text())
     except (OSError,ValueError):excluded['missing_manifest']+=1;continue
     if not infrastructure_failure(m):model_attempts+=1
     if m.get('status')!='complete'or not m.get('validation',{}).get('pass'):continue
     tests={
       'model':model_matches(model,m,run),'track':m.get('track')==track,
       'media':m.get('media_contract')==spec,'initial':m.get('input_image_sha256')==input_hash,
       'prompt':m.get('input_prompt_sha256')==prompt_hash,'rules':m.get('rules_sha256')==rules_hash,
       'cli':m.get('codex_version')=='codex-cli 0.169.0','effort':m.get('reasoning_effort')==model.reasoning_effort,
       'budget':m.get('output_token_budget')==cfg.output_token_budget,
       'fresh_budget':m.get('prior_attempt_output_tokens',0)==0,
       'generation_contract':m.get('generation_contract')=='minimal-v2',
       'additional_inputs':all((m.get('initial_input_hashes')or{}).get(name)==value
                               for name,value in hashes.items()if name not in ('first_frame.png','prompt.txt')),
       'filesystem_policy':compatible_history or m.get('filesystem_policy','workspace-write')==cfg.filesystem_policy,
       'attempt_limit':model_attempts<=5}
     failed=[k for k,v in tests.items()if not v]
     if failed:
      for reason in failed:excluded[reason]+=1
      continue
     # Original validation already checked RGB/PNG parity. Verify the saved
     # delivery still exists; never regenerate or expand its large RGB tensor.
     required=['generate.py','pixels.npz','inspection.png','transition_check.png','index.html',
               'frames/00.png',f"frames/{spec['frames']-1:02d}.png"]
     if any(not(run/'workspace'/p).is_file()or(run/'workspace'/p).stat().st_size==0 for p in required):
      excluded['missing_artifact']+=1;continue
     # Keep the original attempt and source path; no fake rerun directory.
     eligible.append({'model_key':model.key,'track':track,'case_id':cid,'source':str(run),
         'original_attempt':number,'model_attempts_to_delivery':model_attempts,
         'manifest_sha256':sha(run/'run_manifest.json'),'media':spec,
         'source_suite':legacy.name,'original_model':m.get('model'),
         'original_protocol':m.get('protocol'),
         'original_filesystem_policy':m.get('filesystem_policy')or'workspace-write',
         'environment_note':'Original runtime provenance retained; do not call this a new execution.'})
     break
 return {'config':str(config_path),'legacy':str(legacy),'eligible':eligible,
         'counts':dict(collections.Counter(r['model_key']+'/'+r['track']for r in eligible)),
         'exclusion_checks':dict(excluded),'scope':'Source/input/rules/spec/budget/CLI audit; final artifact parity checked by exporter.'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True)
 p.add_argument('--legacy',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--compatible-history',action='store_true',help='Retain the original filesystem provenance while reusing otherwise identical deliveries')
 a=p.parse_args();result=audit(a.config.resolve(),a.legacy.resolve(),a.compatible_history)
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2))
 print(json.dumps({k:v for k,v in result.items()if k not in ['eligible']},indent=2))
