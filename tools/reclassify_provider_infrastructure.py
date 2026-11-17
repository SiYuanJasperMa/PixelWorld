"""Reclassify positively identified provider failures without changing raw traces."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pixelworld.runner import _provider_infra_failure
from pixelworld.retries import infrastructure_failure
from pixelworld.util import atomic_json, sha256, utc_now


STALL_MIN_SECONDS = 20 * 60


def _stall_only(manifest: dict, events: Path) -> bool:
    """Provider accepted the turn but never emitted an item or token."""
    duration = manifest.get('duration_seconds')
    if not isinstance(duration, (int, float)) or duration < STALL_MIN_SECONDS:
        return False
    usage = manifest.get('token_usage') or {}
    if (usage.get('usage_events') or 0) != 0 or isinstance(usage.get('output_tokens'), int):
        return False
    try:
        lines = [line for line in events.read_text(errors='replace').splitlines() if line.strip()]
    except OSError:
        return False
    if not lines or len(lines) > 2:
        return False
    try:
        kinds = [json.loads(line).get('type') for line in lines]
    except ValueError:
        return False
    return kinds and kinds[-1] == 'turn.started' and all(k in ('thread.started', 'turn.started') for k in kinds)


def reclassify(root: Path, apply: bool = False) -> dict:
    def inspect(manifest_path):
        try:manifest=json.loads(manifest_path.read_text())
        except (OSError,ValueError):return None
        if manifest.get('status')!='failed' or manifest.get('stop_reason')=='output_token_budget_exhausted':
            return None
        if infrastructure_failure(manifest):
            return {'already':True,'reason':manifest.get('stop_reason') or
                    (manifest.get('validation') or {}).get('infra_error')}
        events=manifest_path.parent/'logs/events.jsonl'
        reason=_provider_infra_failure(events)
        if not reason and _stall_only(manifest,events):
            reason='provider_first_token_stall'
        if not reason:return None
        old=manifest.get('stop_reason');current=(manifest.get('validation') or {}).get('infra_error')
        if old==reason and current==reason:
            return {'already':True,'reason':reason}
        item={'manifest':str(manifest_path),'previous_stop_reason':old,'reason':reason}
        if apply:
            backup=manifest_path.parent/'logs/run_manifest_before_infra_reclassification.json'
            if not backup.exists():backup.write_bytes(manifest_path.read_bytes())
            validation=manifest.setdefault('validation',{})
            validation['infra_error']=reason
            manifest['stop_reason']=reason;manifest['retryable']=True
            manifest['infrastructure_reclassification']={
                'schema':'pixelworld-positive-infra-v1','updated_utc':utc_now(),
                'raw_events_sha256':sha256(events),'original_manifest':str(backup.relative_to(manifest_path.parent)),
                'pixels_or_code_changed':False,
            }
            atomic_json(manifest_path,manifest)
        return item
    paths=list(root.glob('*/*/case_*/run_manifest.json'))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results=[item for item in pool.map(inspect,paths) if item]
    counts=Counter('already_classified' if item.get('already') else item['reason'] for item in results)
    items=[item for item in results if not item.get('already')]
    return {'root':str(root),'apply':apply,'counts':dict(counts),'changed':len(items),'items':items}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('roots',nargs='+',type=Path);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();reports=[reclassify(root.resolve(),args.apply) for root in args.roots]
    print(json.dumps(reports,indent=2))
