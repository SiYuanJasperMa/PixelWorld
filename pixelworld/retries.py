"""External retry authorizations; never exposed in agent workspaces."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re

from .config import Benchmark, Model
from .util import atomic_json, utc_now

_CACHE: dict[Path, tuple[tuple[int, int], dict]] = {}


def infrastructure_failure(manifest: dict) -> bool:
    """Only positively identified system failures are exempt from model caps."""
    if manifest.get('stop_reason') == 'output_token_budget_exhausted':
        return False
    if manifest.get('stop_reason') == 'token_usage_unavailable':
        evidence = (manifest.get('full_trace') or {}).get('transport_failure') or {}
        from .responses_budget_proxy import TRANSPORT_TYPES
        if not isinstance(evidence, dict):
            return False
        failures = evidence.get('failures')
        if not (manifest.get('status') == 'failed' and manifest.get('cli_backend') in ('opencode', 'claude_code')
            and evidence.get('schema') == 'pixelworld-transport-failure-v1'
            and isinstance(evidence.get('model'), str) and evidence['model'] == manifest.get('model')
            and isinstance(evidence.get('reasoning_effort'), str) and evidence['reasoning_effort'] == manifest.get('reasoning_effort')
            and evidence.get('wire_directory') == 'logs/native/responses_wire'
            and isinstance(failures, list) and failures):
            return False
        def valid_hash(value):
            return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
        return all(isinstance(row, dict) and row.get('type') in TRANSPORT_TYPES
            and isinstance(row.get('request_directory'), str) and re.fullmatch(r'[0-9]+', row['request_directory'])
            and all(valid_hash(row.get(key)) for key in ('request_sha256', 'receipt_sha256'))
            and (valid_hash(row.get('transport_error_sha256')) or
                 row.get('type') == 'StreamDisconnectedBeforeTerminal' and valid_hash(row.get('response_sha256')))
            for row in failures)
    return bool((manifest.get('validation') or {}).get('infra_error')
                or manifest.get('stop_reason') in ('provider_queue_exceeded','provider_connection_failed',
                                                 'storage_infrastructure_failure','local_proxy_request_limit',
                                                 'local_proxy_auth_failure','provider_first_token_stall',
                                                 'local_cli_configuration_failure'))


def ledger_path(config: Benchmark) -> Path:
    return config.runs_root / "retry_authorizations.json"


def ledger(config: Benchmark) -> dict:
    path = ledger_path(config)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"schema_version": 1, "cases": {}}
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached and cached[0] == signature:
        return cached[1]
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or not isinstance(value.get("cases"), dict):
        raise ValueError("Invalid retry authorization ledger")
    _CACHE[path] = (signature, value)
    return value


def windows(config: Benchmark, model: Model, track: str, case: str) -> list[dict]:
    values = ledger(config)["cases"].get(f"{model.key}/{track}/{case}", [])
    for item in values:
        start, end = item.get("start_attempt"), item.get("end_attempt")
        if (not isinstance(start, int) or isinstance(start, bool) or start < 1
                or not isinstance(end, int) or isinstance(end, bool) or end < start):
            raise ValueError("Invalid retry attempt window")
    return values


def latest_window(config: Benchmark, model: Model, track: str, case: str) -> dict | None:
    values = windows(config, model, track, case)
    return values[-1] if values else None


def model_attempt_cap(config: Benchmark, model: Model, track: str, case: str) -> int | None:
    cap=config.max_total_attempts
    for item in windows(config,model,track,case):
        override=item.get('model_attempt_cap')
        if override is None:continue
        if (cap is None or item.get('exception')!='near_complete_rescue'
                or type(override) is not int
                or not config.max_total_attempts <= override <= config.max_total_attempts+2):
            raise ValueError('Invalid scoped near-complete attempt cap')
        cap=max(cap,override)
    return cap


def budget_window(config: Benchmark, model: Model, track: str, case: str, attempt: int) -> dict | None:
    for item in windows(config, model, track, case):
        if item["start_attempt"] <= attempt <= item["end_attempt"]:
            return item
    return None


def authorize_failed(config: Benchmark, extra_attempts: int = 2, max_rounds: int | None = 1,
                     apply: bool = False, cooldown_seconds: float = 0) -> dict:
    from .supervisor import case_ids, configurations, inspect
    if extra_attempts < 1 or (max_rounds is not None and max_rounds < 1) or cooldown_seconds < 0:
        raise ValueError("Retry bounds must be positive")
    config.runs_root.mkdir(parents=True, exist_ok=True)
    path = ledger_path(config)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "cases": {}}
        _CACHE.pop(path, None)
        granted = []
        pending_cases = running_cases = 0
        for model, track in configurations(config):
            parent = config.runs_root / model.key / track
            existing: dict[str, int] = {}
            if parent.is_dir():
                for child in parent.iterdir():
                    match = re.fullmatch(r"case_(.+)_attempt_(\d+)", child.name)
                    if match:
                        existing[match[1]] = max(existing.get(match[1], 0), int(match[2]))
            for case in case_ids(config):
                key = f"{model.key}/{track}/{case}"
                history = data["cases"].get(key, [])
                inspected = inspect(config, model, track, case)
                state = inspected['state']
                pending_cases += state == "pending"
                running_cases += state == "running"
                if ((max_rounds is not None and len(history) >= max_rounds)
                        or state != "exhausted"):
                    continue
                if history and cooldown_seconds:
                    import datetime as dt
                    last = dt.datetime.fromisoformat(history[-1]['authorized_utc'].replace('Z','+00:00'))
                    if (dt.datetime.now(dt.timezone.utc)-last).total_seconds() < cooldown_seconds:
                        continue
                start = max(config.max_attempts, existing.get(case, 0)) + 1
                consumed = inspected.get('model_attempts',0)
                cap=model_attempt_cap(config,model,track,case)
                if cap is not None and consumed >= cap:
                    continue
                available = min(extra_attempts,cap-consumed) if cap is not None else extra_attempts
                item = {"round": len(history) + 1, "start_attempt": start,
                        "end_attempt": start + available - 1,
                        "authorized_utc": utc_now(), "output_token_budget": config.output_token_budget,
                        "model_attempts_before":consumed,
                        "infra_attempts_before":inspected.get('infra_attempts',0),
                        "reason": "User authorized retry of failed samples; old artifacts and usage retained."}
                if cap!=config.max_total_attempts:
                    item.update(exception='near_complete_rescue',model_attempt_cap=cap)
                granted.append({"key": key, **item})
                if apply:
                    data["cases"].setdefault(key, []).append(item)
        if apply and granted:
            data["updated_utc"] = utc_now()
            atomic_json(path, data)
            _CACHE.pop(path, None)
        return {"apply": apply, "authorized_cases": len(granted), "items": granted,
                "pending_cases": pending_cases, "running_cases": running_cases,
                "extra_attempts": extra_attempts, "max_retry_rounds": max_rounds,
                "cooldown_seconds": cooldown_seconds,
                "history_preserved": True}


def authorize_near_complete(config: Benchmark, *, extra_attempts: int = 2,
                            min_fraction: float = .95, max_missing: int | None = None,
                            apply: bool = False) -> dict:
    """Grant one fresh attempt to a sparse terminal tail under user authorization.

    The global cap remains unchanged. This policy never looks at quality scores
    and never relaxes a cohort whose ordinary attempts or infrastructure retries
    are still pending. At most two extra real attempts are allowed per case.
    """
    from .supervisor import case_ids, configurations, inspect
    from .runner import run_path
    from .util import sha256
    if not 1<=extra_attempts<=2 or not 0<min_fraction<=1 or (max_missing is not None and max_missing<1):
        raise ValueError('Invalid sparse rescue policy')
    if config.max_total_attempts is None:return {'authorized_cases':0,'items':[]}
    path=ledger_path(config);config.runs_root.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=json.loads(path.read_text())if path.exists()else{'schema_version':1,'cases':{}}
        _CACHE.pop(path,None)
        changed=False;granted=[];ids=case_ids(config)
        status_path=config.runs_root/'status.json'
        status=json.loads(status_path.read_text())if status_path.exists()else{}
        for model,track in configurations(config):
            # Attach completion evidence to existing scoped authorizations.
            prefix=f'{model.key}/{track}/'
            for key,history in data['cases'].items():
                if not key.startswith(prefix):continue
                for item in history:
                    if item.get('exception')!='near_complete_rescue' or item.get('completed_manifest_sha256'):continue
                    for attempt in range(item['start_attempt'],item['end_attempt']+1):
                        manifest=run_path(config,model,track,key[len(prefix):],attempt)/'run_manifest.json'
                        if not manifest.is_file():continue
                        record=json.loads(manifest.read_text())
                        if record.get('status')=='complete' and (record.get('validation')or{}).get('pass'):
                            item.update(completed_attempt=attempt,completed_manifest_sha256=sha256(manifest));changed=True;break
            hint=status.get('groups',{}).get(f'{model.key}/{track}',{}).get('counts')
            if hint is not None and (hint.get('complete',0)==len(ids) or hint.get('complete',0)<len(ids)*min_fraction):continue
            info={case:inspect(config,model,track,case)for case in ids}
            done=sum(row['state']=='complete'for row in info.values())
            missing=[case for case,row in info.items()if row['state']=='exhausted']
            if (not missing or (max_missing is not None and len(missing)>max_missing) or done<len(ids)*min_fraction
                    or any(row['state']in('pending','running')for row in info.values())):continue
            for case in missing:
                row=info[case];consumed=row.get('model_attempts',0)
                cap=model_attempt_cap(config,model,track,case)
                if consumed<cap or consumed>=config.max_total_attempts+extra_attempts:continue
                existing=[int(p.name.rsplit('_attempt_',1)[1])for p in (config.runs_root/model.key/track).glob(f'case_{case}_attempt_*')]
                start=max([config.max_attempts,*existing])+1
                key=prefix+case;history=data['cases'].setdefault(key,[])
                item={'round':len(history)+1,'start_attempt':start,'end_attempt':start,
                      'authorized_utc':utc_now(),'output_token_budget':config.output_token_budget,
                      'model_attempt_cap':consumed+1,'exception':'near_complete_rescue',
                      'model_attempts_before':consumed,'infra_attempts_before':row.get('infra_attempts',0),
                      'cohort_completed':done,'cohort_expected':len(ids),
                      'reason':'User authorized bounded individual rescues for nearly complete cohorts; no evaluator feedback.'}
                granted.append({'key':key,**item})
                if apply:history.append(item);changed=True
        if apply and changed:
            data['updated_utc']=utc_now();atomic_json(path,data);_CACHE.pop(path,None)
        return {'authorized_cases':len(granted),'items':granted,'apply':apply,
                'global_model_attempt_cap':config.max_total_attempts,'max_extra_model_attempts':extra_attempts}
