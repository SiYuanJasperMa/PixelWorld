"""Run a prompt-only study with existing baseline artifacts and a smoke gate.

This controller uses the unchanged run-case interface and retry accounting.
It never generates pixels, changes model tools, or evaluates physics itself.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from pixelworld.config import load_config
from pixelworld.retries import authorize_failed
from pixelworld.scheduling import memory_launch_allowed
from pixelworld.supervisor import inspect, snapshot
from pixelworld.util import atomic_json, sha256, utc_now
from tools.flash_attempts import accepted

BASELINE_GUIDANCE = 'Aim for high-quality output while considering efficiency.'


def replace_guidance(original: str, guidance: str) -> str:
    if original.count(BASELINE_GUIDANCE) != 1:
        raise ValueError('Expected exactly one baseline efficiency paragraph')
    return original.replace(BASELINE_GUIDANCE, guidance.strip())


def smoke_gate(study: Path, run: Path) -> dict:
    """Require delivery, complete usage, and unchanged initial generation inputs."""
    manifest = json.loads((run / 'run_manifest.json').read_text())
    experiment = json.loads((study / 'experiment_manifest.json').read_text())
    baseline = json.loads(Path(experiment['baseline_records'][manifest['case_id']]).read_text())
    checks = {
        'delivery_pass': bool(accepted(manifest)),
        'usage_complete': manifest.get('token_usage', {}).get('status') == 'recorded',
        'codex_version': manifest.get('codex_version') == baseline.get('codex_version'),
        'baseline_tools': manifest.get('harness_variant') == 'baseline'
                          and manifest.get('flash_tools') is None,
        'vision_boundary': manifest.get('initial_image_attached') is True
                           and manifest.get('output_image_tool_enabled') is True,
        'task_matches': manifest.get('task_sha256') == sha256(study / 'tasks/efficiency.md'),
        'generation_inputs_identical': manifest.get('initial_input_hashes')
                                       == baseline.get('initial_input_hashes'),
        'media_identical': manifest.get('media_contract') == baseline.get('media_contract'),
        'reasoning_effort_identical': manifest.get('reasoning_effort')
                                    == baseline.get('reasoning_effort'),
        'token_budget_identical': manifest.get('output_token_budget')
                                 == baseline.get('output_token_budget'),
    }
    return {'pass': all(checks.values()), 'checks': checks, 'case': manifest['case_id'],
            'attempt': manifest['attempt'], 'updated_utc': utc_now(),
            'scope': 'Technical smoke gate; no quality-preservation claim'}


def run(study: Path, interval: float = 5) -> None:
    experiment = json.loads((study / 'experiment_manifest.json').read_text())
    config = load_config(study / 'configs/prompt.json')
    model = config.model(experiment['model_key'])
    task = study / 'tasks/efficiency.md'
    code = study / 'code_snapshot'
    smoke = experiment['smoke_case']
    children: dict[str, subprocess.Popen] = {}
    state = study / 'controller_state.json'
    with (study / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not (study / 'STOP').exists():
            children = {case: child for case, child in children.items() if child.poll() is None}
            smoke_status = inspect(config, model, 'vision-agent', smoke)
            gate = None
            if smoke_status['state'] == 'complete':
                gate = smoke_gate(study, Path(smoke_status['run']))
                atomic_json(study / 'smoke_gate.json', gate)
                if not gate['pass']:
                    atomic_json(state, {'phase': 'smoke_rejected', **gate})
                    return
            scope = list(config.case_ids) if gate else [smoke]
            # Use the existing cap: five real attempts, infrastructure separate.
            # Restrict grants to the current smoke/matrix scope.
            from dataclasses import replace
            retry_config = replace(config, case_ids=tuple(scope))
            authorize_failed(retry_config, extra_attempts=1, max_rounds=None,
                             apply=True, cooldown_seconds=30)
            for case in scope:
                if len(children) >= (experiment['max_parallel_cases'] if gate else 1):
                    break
                status = inspect(config, model, 'vision-agent', case)
                if status['state'] != 'pending' or case in children:
                    continue
                if not memory_launch_allowed({'min_cgroup_memory_headroom_bytes': 8 * 1024**3}):
                    break
                command = [sys.executable, '-m', 'pixelworld.cli', 'run-case',
                           '--config', str(config.source), '--model', model.key,
                           '--track', 'vision-agent', '--case', case,
                           '--attempt', str(status['attempt']), '--task-file', str(task)]
                env = os.environ.copy()
                env['PYTHONPATH'] = str(code)
                with (study / 'logs' / (case + '.log')).open('ab') as output:
                    children[case] = subprocess.Popen(command, cwd=code, env=env,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True)
                print('launched', case, status['attempt'], children[case].pid, flush=True)
            progress, _ = snapshot(config)
            atomic_json(config.runs_root / 'status.json', progress)
            phase = 'matrix_running' if gate else 'smoke_running'
            atomic_json(state, {'phase': phase, 'controller_pid': os.getpid(),
                'counts': progress['totals'], 'active_children': {k: p.pid for k, p in children.items()},
                'smoke': smoke_status, 'updated_utc': utc_now()})
            terminal = [inspect(config, model, 'vision-agent', case)['state'] for case in scope]
            if not children and all(s in ('complete', 'exhausted') for s in terminal):
                atomic_json(state, {'phase': 'matrix_processed' if gate else 'smoke_failed',
                                   'counts': progress['totals'], 'updated_utc': utc_now()})
                return
            time.sleep(interval)
        atomic_json(state, {'phase': 'explicitly_stopped', 'updated_utc': utc_now()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    run(args.study.resolve(), args.interval)
