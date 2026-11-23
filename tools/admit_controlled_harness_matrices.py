"""Release registered controlled harness cohorts only after all canary gates pass."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from pixelworld.config import CONTROLLED_CLI_VERSIONS, load_config
from pixelworld.constants import PHYSICS_IQ_COMMIT
from pixelworld.util import atomic_json, sha256, utc_now


def read(path):
    return json.loads(path.read_text())


def verified_canary(config, model, case, archives):
    run = config.runs_root/model/'vision-agent'/f'case_{case}_attempt_01'
    manifest, local = run/'run_manifest.json', run/'verified_archive_status.json'
    if not manifest.is_file() or not local.is_file():
        return None
    m,a = read(manifest),read(local)
    validation = m.get('validation') or {}
    if (m.get('status') != 'complete' or validation.get('pass') is not True or
        validation.get('frame0_exact') is not True or validation.get('frames') != 120 or
        validation.get('png_tensor_exact') is not True or m.get('token_usage',{}).get('status') != 'recorded'):
        return None
    entry = config.model(model)
    if (m['model_key'],m['model'],m['reasoning_effort'],m['track'],str(m['case_id']),m['attempt']) != (
        model,entry.model,entry.reasoning_effort,'vision-agent',case,1
    ) or not (m.get('token_budget') or {}).get('budget_tokens') == config.output_token_budget:
        raise ValueError('Model, case or budget differs from the registered canary')
    if a.get('status') != 'verified' or not a['known_requests'] == a['wire_pair_count'] > 0:
        return None
    name = a['benchmark']
    if name != 'physics_iq_720p24fps-'+entry.cli_backend:
        raise ValueError('Unexpected canary archive scope')
    proof = archives/name/model/f"run_{a['run_number']:04d}"/'verified.json'
    if not proof.is_file():
        return None
    receipt = read(proof)
    if (receipt['source_run'] != str(run) or receipt['members']['run_manifest.json'] != sha256(manifest) or
        receipt['verification'] != 'independent SHA256 member readback' or
        len(receipt['known_request_roles']) != a['known_requests']):
        raise ValueError('Archive proof is not bound to the exact delivered canary')
    return run, manifest, a


def admit(args):
    registry = args.registry.resolve(strict=True)
    site = registry.parent
    os.umask(0o077)
    with (site/'release.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = read(registry)
        for model, rows in state['models'].items():
            for backend, row in rows.items():
                if row['full_supervisor_started']:
                    continue
                fn = Path(row['config']).resolve(strict=True)
                if (backend not in CONTROLLED_CLI_VERSIONS or not fn.is_relative_to(site) or
                        sha256(fn) != row['config_sha256']):
                    raise ValueError('Registered native matrix changed')
                c = load_config(fn)
                if (len(c.case_ids or ()) != 198 or len(set(c.case_ids)) != 198 or
                        c.model(model).cli_backend != backend or row['canary_case'] not in c.case_ids):
                    raise ValueError('Unexpected Physics-IQ matrix scope')
                proof = verified_canary(c,model,row['canary_case'],args.archive)
                if not proof:
                    continue
                run, manifest, archive = proof
                destination = fn.parent/f'official_{backend}_canary'
                evaluator = destination/'smoke_status.json'
                if not evaluator.is_file():
                    if destination.exists():
                        print(json.dumps({'model':model,'backend':backend,'blocked':'existing evaluator output without status'}),flush=True)
                        continue
                    result = subprocess.run([sys.executable,'-m','tools.evaluate_physics_smoke','--run',str(run),
                         '--resource-root',str(args.resource_root),'--output',str(destination),'--per-view-smoke'])
                    if result.returncode != 0 or not evaluator.is_file():
                        print(json.dumps({'model':model,'backend':backend,'blocked':'official canary evaluation failed'}),flush=True)
                        continue
                q = read(evaluator)
                if q.get('status') != 'complete':
                    continue
                if (q.get('case_id'), q.get('official_commit'), Path(q['run']).resolve()) != (
                    row['canary_case'], PHYSICS_IQ_COMMIT, run.resolve()
                ) or q.get('exit_code') != 0:
                    raise ValueError('Official smoke did not score the pinned canary')
                with (c.runs_root/'matrix.lock').open('a') as matrix:
                    fcntl.flock(matrix,fcntl.LOCK_EX|fcntl.LOCK_NB)
                log = fn.parent/f'{backend}_matrix.log'
                with log.open('xb') as output:
                    child = subprocess.Popen([str(args.runtime/'bin/python'),'-u','-m','pixelworld.cli','matrix','run',
                         '--config',str(fn),'--interval','20'],cwd=args.repo,
                         env={**os.environ,'PIXELWORLD_ARCHIVE_BENCHMARK':archive['benchmark']},
                         stdout=output,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
                time.sleep(.5)
                if child.poll() is not None:
                    raise RuntimeError('Matrix supervisor did not stay running')
                row.update(full_supervisor_started=True,supervisor_pid=child.pid,started_utc=utc_now(),
                           acceptance_state='verified',canary_manifest_sha256=sha256(manifest),
                           canary_official_receipt_sha256=sha256(evaluator))
                atomic_json(registry,state)
                print(json.dumps({'model':model,'backend':backend,'pid':child.pid,'registered':len(c.case_ids),
                                  'reused_canary':row['canary_case']}),flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('registry','archive','repo','runtime','resource-root'):
        p.add_argument('--'+name,type=Path,required=True)
    admit(p.parse_args())


if __name__ == '__main__':
    main()
