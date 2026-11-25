"""Recover closed, valid deliveries rejected only by the old static path audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pixelworld.audit import validate
from pixelworld.submission import safe_outputs
from pixelworld.util import atomic_json, sha256, utc_now


def recover(run: Path, *, apply=False) -> dict:
    run=run.resolve();path=run/'run_manifest.json';before=path.read_bytes();m=json.loads(before)
    old=m.get('validation') or {};row={'run':str(run),'eligible':False,'applied':False}
    if (m.get('status')!='failed' or not old.get('outside_workspace_command_paths')
            or m.get('exit_status',old.get('exit_status'))!=0
            or old.get('infra_error') or m.get('stop_reason')
            or type(old.get('planning_and_method_reports_required')) is not bool):
        return row
    workspace=run/'workspace';logs=run/'logs';spec=m.get('media_contract')
    if not isinstance(spec,dict):return row
    safe_outputs(workspace,spec)
    names=['generate.py','pixels.npz']
    if any(not (workspace/n).is_file() for n in names):
        return {**row,'reason':'required_outputs_missing'}
    hashes={n:sha256(workspace/n) for n in names}
    for name,field in [('generate.py','generate_sha256'),('pixels.npz','pixels_sha256')]:
        if m.get(field) and m[field]!=hashes[name]:
            return {**row,'reason':'source_changed_since_failure'}
    result=validate(workspace,logs/'events.jsonl',0,m['track'],
                    require_reports=old['planning_and_method_reports_required'],media=spec)
    if not result['pass']:return {**row,'remaining_validation':result}
    if path.read_bytes()!=before or any(sha256(workspace/n)!=digest for n,digest in hashes.items()):
        return {**row,'reason':'source_changed_during_validation'}
    row.update(eligible=True,removed_flags=old['outside_workspace_command_paths'],source_hashes=hashes)
    if apply:
        backup=logs/'run_manifest_before_audit_revalidation.json'
        if not backup.exists():
            with backup.open('xb') as f:f.write(before)
            backup.chmod(0o600)
        if backup.read_bytes()!=before:raise ValueError('A different original recovery already exists')
        saved=logs/'validation_before_audit_revalidation.json'
        if not saved.exists():atomic_json(saved,old)
        result={**old,**result,'track':m['track']}
        m.update(status='complete',validation=result,retryable=False)
        m['audit_revalidation']={'schema':'pixelworld-static-audit-recovery-v1','checked_utc':utc_now(),
            'original_manifest':str(backup.relative_to(run)),'original_manifest_sha256':sha256(backup),
            'audit_source_sha256':sha256(Path(__file__).resolve().parents[1]/'pixelworld/audit.py'),
            'removed_false_paths':row['removed_flags'],'source_hashes':hashes,'pixels_or_code_changed':False}
        atomic_json(logs/'validation.json',result);atomic_json(path,m);row['applied']=True
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('runs',type=Path,nargs='+')
    p.add_argument('--apply',action='store_true');p.add_argument('--report',type=Path)
    a=p.parse_args();rows=[recover(run,apply=a.apply) for run in a.runs]
    result={'updated_utc':utc_now(),'records':rows,'restored':sum(r['applied'] for r in rows)}
    if a.report:atomic_json(a.report,result)
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))


if __name__=='__main__':main()
