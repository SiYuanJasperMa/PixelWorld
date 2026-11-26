"""Recover closed, valid deliveries rejected only by the old static path audit."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from pixelworld.audit import validate, _unwrap_shell
from pixelworld.submission import safe_outputs
from pixelworld.util import atomic_json, sha256, utc_now


def recover(run: Path, *, apply=False, original_run: Path | None = None) -> dict:
    run=run.resolve();path=run/'run_manifest.json';before=path.read_bytes();m=json.loads(before)
    old=m.get('validation') or {};row={'run':str(run),'eligible':False,'applied':False}
    if (m.get('status')!='failed' or not old.get('outside_workspace_command_paths')
            or m.get('exit_status',old.get('exit_status'))!=0
            or old.get('infra_error') or m.get('stop_reason')
            or type(old.get('planning_and_method_reports_required')) is not bool):
        return row
    workspace=run/'workspace';logs=run/'logs';spec=m.get('media_contract')
    if not isinstance(spec,dict):return row
    if apply:
        # Detect quota/read-only failures before repeatedly hashing and decoding
        # hundreds of frames. The probe is outside the agent's workspace.
        with tempfile.NamedTemporaryFile(prefix='.audit_recovery_probe.', dir=logs) as probe:
            probe.write(b'audit-recovery\n')
            probe.flush()
            os.fsync(probe.fileno())
    safe_outputs(workspace,spec)
    names=['generate.py','pixels.npz']
    if any(not (workspace/n).is_file() for n in names):
        return {**row,'reason':'required_outputs_missing'}
    hashes={n:sha256(workspace/n) for n in names}
    for name,field in [('generate.py','generate_sha256'),('pixels.npz','pixels_sha256')]:
        if m.get(field) and m[field]!=hashes[name]:
            return {**row,'reason':'source_changed_since_failure'}
    events = logs/'events.jsonl'
    alias_projection = None
    if original_run is not None:
        if not apply:
            raise ValueError('Relocation projection requires a writable verified copy')
        original_run = original_run.resolve()
        if (original_run/'run_manifest.json').read_bytes() != before:
            raise ValueError('Relocated manifest differs from its original')
        for relative in ['workspace/generate.py', 'workspace/pixels.npz', 'logs/events.jsonl', 'permissions_profile.json']:
            source, copy = original_run/relative, run/relative
            if source.exists() != copy.exists() or (source.exists() and sha256(source) != sha256(copy)):
                raise ValueError('Relocated copy differs at ' + relative)
        aliases = {str(original_run/'workspace'): str(workspace.resolve())}
        if isinstance(m.get('workspace'), str):
            aliases[m['workspace']] = str(workspace.resolve())
        original_workspace = (original_run/'workspace').resolve()
        def original_directory(value):
            value = str(value) if value else str(original_workspace)
            for old in sorted(aliases, key=len, reverse=True):
                if value == old or value.startswith(old+'/'):
                    return original_workspace/value[len(old):].lstrip('/')
            path = Path(value)
            return path if path.is_absolute() else original_workspace/path
        def parent_aliases(item):
            command = item.get('command')
            if not isinstance(command, str):
                return item
            script = _unwrap_shell(command)
            cwd = original_directory(item.get('working_directory') or item.get('cwd'))
            prefix = re.match(r"^\s*cd\s+(?P<path>'[^'\n]+'|\"[^\"\n]+\"|[^\s;&|<>]+)\s*&&\s*", script)
            if prefix:
                import shlex
                token = shlex.split(prefix.group('path'))[0]
                if any(symbol in token for symbol in ('$', '`', '~')):
                    return item
                cwd = original_directory(token) if Path(token).is_absolute() else cwd/token
                script = script[prefix.end():]
            if cwd.resolve() != original_workspace and original_workspace not in cwd.resolve().parents:
                return item
            if re.search(r"(?:^|[;\n&|])\s*(?:cd|pushd|popd)\b|\b(?:os|posix)\.chdir\s*\(", script):
                return item
            for literal in re.findall(r"(?:^|[\s'\"])((?:\.\./)+[^\s'\";&|<>]*)", script):
                if any(symbol in literal for symbol in ('$','`','\\')):
                    continue
                target = (cwd/literal.rstrip('),:]')).resolve()
                if target == original_workspace or original_workspace in target.parents:
                    replacement = str(workspace.resolve()/target.relative_to(original_workspace))
                    command = command.replace(literal, replacement)
            return {**item, 'command': command}
        def project(value):
            if isinstance(value, dict):
                if value.get('type') == 'command_execution':
                    value = parent_aliases(value)
                return {key: project(item) for key, item in value.items()}
            if isinstance(value, list):
                return [project(item) for item in value]
            if isinstance(value, str):
                for old, new in sorted(aliases.items(), key=lambda pair: -len(pair[0])):
                    value = re.sub(re.escape(old) + r"(?=$|[/\s'\"\\;,)\]}])", lambda _: new, value)
            return value
        projected = logs/'events_for_audit_revalidation.jsonl'
        with events.open() as source, projected.open('w') as copy:
            for line in source:
                try:
                    copy.write(json.dumps(project(json.loads(line)), ensure_ascii=False)+'\n')
                except json.JSONDecodeError:
                    copy.write(line)
        alias_projection = {'original_run': str(original_run), 'aliases': aliases,
            'original_events_sha256': sha256(events), 'projected_events_sha256': sha256(projected),
            'original_events_changed': False}
        events = projected
    result=validate(workspace,events,0,m['track'],
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
        if alias_projection:
            m['audit_revalidation']['workspace_alias_projection'] = alias_projection
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
