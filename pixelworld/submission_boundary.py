"""Verified submission handoff and actual per-request usage; no synthetic model output."""
import json
from pathlib import Path

from .audit import validate
from .media import contract
from .submission import safe_outputs
from .util import atomic_json, sha256, utc_now

STOP_MESSAGE = 'pixelworld_submission_accepted_at_request_boundary'


def accept_boundary(workspace, request_sha256):
    run = workspace.parent
    manifest = json.loads((run/'run_manifest.json').read_text())
    if manifest.get('harness_variant') != 'flash-submit-boundary' or manifest.get('filesystem_policy') != 'case-only-v1':
        return None
    path = run/'logs/submission_receipt.json'
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    if not receipt.get('accepted'):
        return None
    hashes = receipt.get('artifact_sha256') or {}
    required = ('generate.py','pixels.npz','inspection.png','transition_check.png','index.html')
    if set(hashes) != set(required):
        return None
    spec = contract(run/'media_contract.json')
    safe_outputs(workspace, spec)
    if any(sha256(workspace/name) != hashes[name] for name in required):
        return None
    frame_hashes={str(path.relative_to(workspace)):sha256(path) for path in sorted((workspace/'frames').glob('*.png'))}
    checked = validate(workspace, run/'logs/events.jsonl', -1, manifest['track'],
                       require_reports=False, media=spec, submission_requested=True)
    if not checked['pass'] or checked['generate_runs'] < 1:
        return None
    if any(sha256(workspace/name) != hashes[name] for name in required):
        return None
    if any(sha256(workspace/name)!=value for name,value in frame_hashes.items()):
        return None
    boundary = {'schema':'pixelworld-submission-boundary-v1','utc':utc_now(),
                'accepted':True,'pre_upstream':True,'message':STOP_MESSAGE,
                'request_sha256':request_sha256,'receipt_sha256':sha256(path),
                'artifact_sha256':hashes,'frame_sha256':frame_hashes,'validation':checked}
    atomic_json(run/'logs/submission_boundary.json', boundary)
    return boundary


def completed_usage(run, terminal_usage):
    """Only fully completed gateway records support a full token-cost claim."""
    boundary_path = run/'logs/submission_boundary.json'
    if not boundary_path.exists():
        return terminal_usage
    boundary = json.loads(boundary_path.read_text())
    if not boundary.get('accepted') or not boundary.get('pre_upstream'):
        return terminal_usage
    records = [json.loads(path.read_text()) for path in sorted((run/'logs/submission_requests').glob('*.json'))]
    if (not records or any(row.get('phase') != 'completed' or not row.get('response_id') for row in records)
            or len({row['response_id'] for row in records}) != len(records)):
        return terminal_usage
    fields = ('input_tokens','output_tokens')
    if any(not isinstance(row.get('usage',{}).get(key),int) or isinstance(row['usage'][key],bool) or row['usage'][key]<0
           for row in records for key in fields):
        return terminal_usage
    total={key:sum(row['usage'][key] for row in records) for key in fields}
    trace=run/'logs/full_rollout.jsonl'
    cumulative=None
    if trace.exists():
        for line in trace.open():
            event=json.loads(line);payload=event.get('payload')or{}
            if event.get('type')=='event_msg' and payload.get('type')=='token_count':
                value=(payload.get('info')or{}).get('total_token_usage')or{}
                if all(isinstance(value.get(key),int) for key in fields):cumulative=value
    if not cumulative or any(cumulative[key]!=total[key] for key in fields):
        return terminal_usage
    cached = [row['usage'].get('input_tokens_details',{}).get('cached_tokens') for row in records]
    reasoning = [row['usage'].get('output_tokens_details',{}).get('reasoning_tokens') for row in records]
    return {**terminal_usage,'status':'recorded',
            'input_tokens':total['input_tokens'], 'output_tokens':total['output_tokens'],
            'cached_input_tokens':sum(cached) if all(isinstance(x,int) for x in cached) else None,
            'reasoning_output_tokens':sum(reasoning) if all(isinstance(x,int) for x in reasoning) else None,
            'source':'Completed gateway Responses usage at verified pre-upstream submission handoff',
            'raw_terminal_usage':terminal_usage,'gateway_completed_records':len(records),
            'boundary_sha256':sha256(boundary_path),'inflight_usage_unknown':False,
            'counter_crosscheck':'Completed gateway usage equals native cumulative Codex counters',
            'reasoning_inclusion':'Provider-reported output preserved; reasoning not added'}


def _verify_boundary(run):
    path = run/'logs/submission_boundary.json'
    if not path.exists():
        return False
    manifest = json.loads((run/'run_manifest.json').read_text())
    if manifest.get('harness_variant') != 'flash-submit-boundary':
        return False
    boundary = json.loads(path.read_text())
    if not boundary.get('accepted') or not boundary.get('pre_upstream'):
        return False
    required={'generate.py','pixels.npz','inspection.png','transition_check.png','index.html'}
    if set(boundary.get('artifact_sha256',{}))!=required:
        return False
    if sha256(run/'logs/submission_receipt.json')!=boundary.get('receipt_sha256'):
        return False
    safe_outputs(run/'workspace', contract(run/'media_contract.json'))
    spec=contract(run/'media_contract.json')
    if set(boundary.get('frame_sha256',{}))!={f'frames/{index:02d}.png' for index in range(spec['frames'])}:
        return False
    if any(sha256(run/'workspace'/name)!=value for name,value in boundary['frame_sha256'].items()):
        return False
    if any(sha256(run/'workspace'/name) != value for name,value in boundary['artifact_sha256'].items()):
        return False
    events = run/'logs/events.jsonl'
    return events.exists() and any(
        event.get('type') in ('error','turn.failed') and STOP_MESSAGE in json.dumps(event)
        for event in (json.loads(line) for line in events.read_text().splitlines() if line.strip()))


def verify_boundary(run):
    try:
        return _verify_boundary(run)
    except (OSError, ValueError, KeyError):
        return False
