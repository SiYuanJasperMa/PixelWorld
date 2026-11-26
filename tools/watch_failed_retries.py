"""Authorize bounded retries for terminal failed cases.

Public policy is expressed as a total per-case model-attempt cap.
"""
from pathlib import Path
import argparse
import json
import sys
import time
import os
import subprocess
import signal
import fcntl
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixelworld.config import load_config
from pixelworld.retries import authorize_failed, authorize_near_complete
from pixelworld.util import atomic_json, utc_now


def ensure_manager(config, entry):
    lock_path = Path(entry['pid_file']).with_suffix('.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_manager(config, entry)


def _ensure_manager(config, entry):
    def command(pid):
        try:return (Path('/proc')/str(pid)/'cmdline').read_bytes()
        except OSError:return b''
    pid_file=Path(entry['pid_file']);state=Path(entry['state_file']);code=Path(entry['code_root'])
    if pid_file.exists():
        pid=pid_file.read_text().strip();cmdline=command(pid)
        if b'manage_matrix.py' in cmdline and str(config.source).encode() in cmdline:
            return {'phase':'manager_active','pid':int(pid)}
    status_file=config.runs_root/'status.json'
    if status_file.exists():
        status=json.loads(status_file.read_text());cmdline=command(status.get('supervisor_pid'))
        if not (str(config.source).encode() in cmdline and b'matrix' in cmdline):
            status['supervisor_pid']=None;atomic_json(status_file,status)
    state.parent.mkdir(parents=True,exist_ok=True);pid_file.parent.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,'-u',str(Path(__file__).with_name('manage_matrix.py')),'--config',str(config.source),
         '--code-root',str(code),'--state',str(state)]
    with state.with_suffix('.retry-manager.log').open('ab') as log:
        process=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 start_new_session=True,env=os.environ.copy(),cwd=code)
    pid_file.write_text(str(process.pid)+'\n')
    return {'phase':'manager_restarted','pid':process.pid,'code_root':str(code)}


def needs_manager(result):
    return any(result.get(key,0) for key in ('authorized_cases','pending_cases','running_cases'))


def unfinished_retry_scope(config):
    """Skip cohorts already reported complete; retain the original manager config."""
    from pixelworld.supervisor import case_ids
    expected = len(case_ids(config))
    try:
        groups = json.loads((config.runs_root/'status.json').read_text()).get('groups', {})
    except (OSError, ValueError):
        return config
    models = []
    for model in config.models:
        tracks = tuple(track for track in model.tracks
                       if groups.get(f'{model.key}/{track}', {}).get('counts', {}).get('complete') != expected)
        if tracks:
            models.append(replace(model, tracks=tracks))
    return replace(config, models=tuple(models))


def recover_audit_before_retry(config, apply):
    """Defer grants when a valid historical artifact needs writable-host repair."""
    from tools.revalidate_audit_failures import recover
    restored=[];pending=[];errors=[];held=set()
    from pixelworld.supervisor import audit_recovery_holds
    prior_holds = audit_recovery_holds(config)
    prefixes = tuple(f'{model.key}/{track}/' for model in config.models for track in model.tracks)
    held.update(key for key in prior_holds if not key.startswith(prefixes))
    try:groups=json.loads((config.runs_root/'status.json').read_text()).get('groups',{})
    except (OSError,ValueError):groups={}
    for model in config.models:
        for track in model.tracks:
            leaf=config.runs_root/model.key/track
            group=groups.get(f'{model.key}/{track}',{})
            if 'exhausted_cases' in group:
                prefix=f'{model.key}/{track}/'
                cases=set(group['exhausted_cases']) | {key[len(prefix):] for key in prior_holds if key.startswith(prefix)}
                paths=[p for case in cases for p in leaf.glob(f'case_{case}_attempt_*/run_manifest.json')]
            else:paths=leaf.glob('case_*/run_manifest.json')
            for path in paths:
                try:
                    m=json.loads(path.read_text());v=m.get('validation') or {}
                    if m.get('status')!='failed' or not v.get('outside_workspace_command_paths'):continue
                    if v.get('missing_required') or not v.get('frame0_exact') or not v.get('png_tensor_exact'):continue
                    report=recover(path.parent,apply=apply)
                    if report.get('applied'):restored.append(str(path.parent))
                except OSError:
                    pending.append(str(path.parent))
                    case = path.parent.name.removeprefix('case_').rsplit('_attempt_', 1)[0]
                    held.add(f'{model.key}/{track}/{case}')
                    error = sys.exc_info()[1]
                    errors.append({'run': str(path.parent), 'errno': error.errno,
                                   'filename': error.filename, 'message': str(error)})
    if apply:
        atomic_json(config.runs_root/'audit_recovery_pending.json',
                    {'schema_version': 1, 'cases': sorted(held), 'updated_utc': utc_now()})
    return {'restored':restored,'pending_writable_host':pending,'write_errors':errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, action="append", required=True)
    parser.add_argument('--model-key', action='append', default=[],
                        help='Scope recovery and retry grants to these model keys without changing matrix configuration.')
    parser.add_argument("--extra-attempts", type=int, default=1,
                        help='Attempts granted per retry authorization window.')
    parser.add_argument("--max-total-attempts", type=int, default=5,
                        help='Maximum total model attempts per case, including the original attempt window.')
    parser.add_argument("--max-rounds", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument('--continuous',action='store_true',
                        help='Continue monitoring newly exhausted cases while enforcing the total attempt cap.')
    parser.add_argument('--cooldown',type=float,default=300,help='Minimum seconds between retry grants for a case in continuous mode')
    parser.add_argument('--manager-map',type=Path,help='Runtime config-path to original manager/code checkout map')
    parser.add_argument('--near-complete-extra-attempts',type=int,default=0,
                        help='User-authorized sparse terminal rescues, at most two extra real attempts')
    parser.add_argument('--near-complete-until-delivery',action='store_true',
                        help='Requires explicit owner authorization: retry a sparse terminal tail until first valid delivery')
    parser.add_argument('--retry-all-failed-model', action='append', default=[],
                        help='Explicit owner authorization for this exact model key: finish all failed cases, not only a sparse tail')
    parser.add_argument('--audit-revalidate',action='store_true',
                        help='Revalidate closed audit-only rejections before granting any regeneration')
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()
    if args.interval < 1:
        raise ValueError("Watch interval must be positive")
    managers=json.loads(args.manager_map.read_text()) if args.manager_map else {}
    while True:
        reports = []
        for path in args.config:
            if args.status:
                atomic_json(args.status, {'updated_utc':utc_now(), 'phase':'checking_config',
                                          'current_config':str(path), 'reports':reports})
            config = load_config(path)
            if args.model_key:
                requested = set(args.model_key)
                if not requested.issubset({model.key for model in config.models}):
                    raise ValueError('Requested retry model is absent from the matrix')
                config = replace(config, models=tuple(model for model in config.models if model.key in requested))
            max_total_attempts = args.max_total_attempts
            if max_total_attempts is not None:
                if config.max_total_attempts is not None and config.max_total_attempts != max_total_attempts:
                    raise ValueError(f"{path} sets max_total_attempts={config.max_total_attempts}, "
                                     f"but watcher was launched with --max-total-attempts {max_total_attempts}")
                if config.max_total_attempts is None:
                    config = replace(config, max_total_attempts=max_total_attempts)
            # Infrastructure records can raise directory ordinals without
            # spending a model attempt. The model cap itself bounds grants;
            # a derived ordinal-based round cap would stop these retries early.
            max_rounds = args.max_rounds
            if (config.runs_root / "STOP").exists():
                reports.append({"config": str(path), "phase": "explicitly_stopped"})
                continue
            if args.audit_revalidate:
                audit=recover_audit_before_retry(unfinished_retry_scope(config),args.apply)
            # Continuous monitoring is not permission for unbounded retries.
            # The externally visible policy is the total model-attempt cap.
            # Ledger windows identify new run directories; their ordinals do
            # not measure the number of model attempts.
            result = authorize_failed(unfinished_retry_scope(config), args.extra_attempts, max_rounds,
                                      args.apply, args.cooldown if args.continuous else 0)
            if args.audit_revalidate:
                result['audit_recovery'] = audit
                if audit['pending_writable_host']:
                    result['phase'] = 'audit_revalidation_deferred_cases'
            if args.retry_all_failed_model:
                recovery=authorize_near_complete(config, min_fraction=0, max_missing=None,
                    until_first_delivery=True, model_keys=set(args.retry_all_failed_model), apply=args.apply)
                result['owner_model_recovery']=recovery
                result['authorized_cases']+=recovery['authorized_cases']
            if args.near_complete_extra_attempts or args.near_complete_until_delivery:
                rescue=authorize_near_complete(config,extra_attempts=args.near_complete_extra_attempts or 2,
                    min_fraction=.85 if args.near_complete_until_delivery else .95,
                    max_missing=10 if args.near_complete_until_delivery else None,
                    until_first_delivery=args.near_complete_until_delivery,apply=args.apply)
                result['near_complete_rescue']=rescue
                result['authorized_cases']+=rescue['authorized_cases']
                if args.apply and rescue['authorized_cases']:
                    status_path=config.runs_root/'status.json'
                    if status_path.exists():
                        status=json.loads(status_path.read_text());pid=status.get('supervisor_pid')
                        try:
                            argv=[x.decode()for x in(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')if x]
                            if 'pixelworld.cli' in argv and 'matrix' in argv and str(config.source) in argv:
                                os.kill(pid,signal.SIGTERM)
                                status['supervisor_pid']=None;atomic_json(status_path,status)
                        except OSError:pass
            if args.max_rounds is None and max_total_attempts is not None:
                result.pop('max_retry_rounds', None)
            result['attempt_policy'] = {
                'max_total_attempts': max_total_attempts,
                'extra_attempts_per_retry_window': args.extra_attempts,
                'token_budget_exhausted_counts_as_failed_attempt': True,
                'infra_failures_count_as_model_attempt': False,
                'scoped_models': [model.key for model in config.models],
            }
            entry=managers.get(str(config.source))
            if needs_manager(result) and args.apply and entry:
                result['manager']=ensure_manager(config,entry)
            reports.append({"config": str(path), **result})
        result = {"updated_utc": utc_now(), "reports": reports}
        if args.status:
            atomic_json(args.status, result)
        print(json.dumps({"updated_utc": result["updated_utc"],
                          "configs": [{"config": r["config"], "authorized_cases": r.get("authorized_cases", 0),
                                       "phase": r.get("phase")} for r in reports]}), flush=True)
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
