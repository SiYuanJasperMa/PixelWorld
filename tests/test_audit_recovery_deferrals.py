import json
import sys
import pytest
from pathlib import Path

from pixelworld.config import load_config
from pixelworld.runner import run_path
from pixelworld.supervisor import inspect
from tools import revalidate_audit_failures, watch_failed_retries
from pixelworld.util import sha256


def make_cases(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'inputs_root': 'inputs', 'runs_root': 'runs',
        'case_ids': ['blocked', 'retry'], 'max_attempts': 1, 'max_total_attempts': 5,
        'models': [{'key': 'm', 'model': 'fixture', 'tracks': ['text']}]}))
    config = load_config(path)
    model = config.models[0]
    for case in config.case_ids:
        run = run_path(config, model, 'text', case, 1)
        run.mkdir(parents=True)
        value = {'status': 'failed', 'exit_status': 0, 'validation': {'pass': False}}
        if case == 'blocked':
            value['validation'].update(outside_workspace_command_paths=['/old/workspace'],
                frame0_exact=True, png_tensor_exact=True, missing_required=[])
        (run / 'run_manifest.json').write_text(json.dumps(value))
    return path, config, model


def test_quota_blocked_audit_does_not_stop_other_retries(tmp_path, monkeypatch):
    path, config, model = make_cases(tmp_path)
    original = (run_path(config, model, 'text', 'blocked', 1) / 'run_manifest.json').read_bytes()

    def quota_error(run, *, apply):
        raise OSError(122, 'Disk quota exceeded', str(run / 'logs/backup.json'))

    monkeypatch.setattr(revalidate_audit_failures, 'recover', quota_error)
    report = tmp_path / 'retry-report.json'
    monkeypatch.setattr(sys, 'argv', ['retry', '--config', str(path), '--apply',
        '--audit-revalidate', '--max-total-attempts', '5', '--status', str(report)])
    watch_failed_retries.main()
    result = json.loads(report.read_text())['reports'][0]
    assert result['phase'] == 'audit_revalidation_deferred_cases'
    assert result['authorized_cases'] == 1
    assert result['items'][0]['key'] == 'm/text/retry'
    assert result['audit_recovery']['write_errors'][0]['errno'] == 122
    held = inspect(config, model, 'text', 'blocked')
    assert held['state'] == 'pending' and held['blocked_by_audit_revalidation']
    assert inspect(config, model, 'text', 'retry')['state'] == 'pending'
    assert (run_path(config, model, 'text', 'blocked', 1) / 'run_manifest.json').read_bytes() == original
    assert config.max_total_attempts == 5


def test_deferred_case_remains_in_recovery_scan_after_snapshot(tmp_path, monkeypatch):
    _, config, model = make_cases(tmp_path)
    (config.runs_root / 'audit_recovery_pending.json').write_text(json.dumps(
        {'schema_version': 1, 'cases': ['m/text/blocked']}))
    (config.runs_root / 'status.json').write_text(json.dumps({'groups': {
        'm/text': {'exhausted_cases': ['retry']}}}))
    calls = []

    def quota_error(run, *, apply):
        calls.append(run.name)
        raise OSError(122, 'Disk quota exceeded')

    monkeypatch.setattr(revalidate_audit_failures, 'recover', quota_error)
    result = watch_failed_retries.recover_audit_before_retry(config, True)
    assert calls == ['case_blocked_attempt_01']
    assert len(result['pending_writable_host']) == 1


def test_valid_delivery_wins_over_stale_audit_deferral(tmp_path):
    _, config, model = make_cases(tmp_path)
    (config.runs_root / 'audit_recovery_pending.json').write_text(json.dumps(
        {'schema_version': 1, 'cases': ['m/text/blocked']}))
    path = run_path(config, model, 'text', 'blocked', 1) / 'run_manifest.json'
    path.write_text(json.dumps({'status': 'complete', 'validation': {'pass': True}}))
    assert inspect(config, model, 'text', 'blocked')['state'] == 'complete'


@pytest.mark.parametrize('task,exact,satisfied,expected', [
    ('v2v', None, True, True), ('t2v', None, True, True),
    ('i2v', False, True, False), ('v2v', None, False, False),
])
def test_recovery_uses_the_task_specific_initial_frame_contract(tmp_path, monkeypatch, task, exact, satisfied, expected):
    _, config, model = make_cases(tmp_path)
    run = run_path(config, model, 'text', 'blocked', 1)
    path = run/'run_manifest.json'
    manifest = json.loads(path.read_text())
    manifest['validation'].update(frame0_exact=exact, first_frame_constraint_satisfied=satisfied,
                                  media_contract={'task_type':task})
    path.write_text(json.dumps(manifest))
    calls=[]

    def recovered(candidate, *, apply):
        calls.append(candidate)
        return {'run':str(candidate), 'eligible':True, 'applied':apply}

    monkeypatch.setattr(revalidate_audit_failures, 'recover', recovered)
    result = watch_failed_retries.recover_audit_before_retry(config, True)
    assert calls == ([run] if expected else [])
    assert result['restored'] == ([str(run)] if expected else [])


def test_model_scoped_recovery_preserves_other_models_audit_deferrals(tmp_path, monkeypatch):
    path, config, model = make_cases(tmp_path)
    raw = json.loads(path.read_text())
    raw['models'].append({'key': 'other', 'model': 'fixture', 'tracks': ['text']})
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    (config.runs_root/'audit_recovery_pending.json').write_text(json.dumps(
        {'schema_version': 1, 'cases': ['other/text/blocked']}))

    def quota_error(run, *, apply):
        raise OSError(122, 'Disk quota exceeded')

    monkeypatch.setattr(revalidate_audit_failures, 'recover', quota_error)
    report = tmp_path/'scoped-report.json'
    monkeypatch.setattr(sys, 'argv', ['retry', '--config', str(path), '--apply',
        '--audit-revalidate', '--max-total-attempts', '5', '--model-key', 'm', '--status', str(report)])
    watch_failed_retries.main()
    row = json.loads(report.read_text())['reports'][0]
    assert row['attempt_policy']['scoped_models'] == ['m']
    assert row['authorized_cases'] == 1
    holds = json.loads((config.runs_root/'audit_recovery_pending.json').read_text())['cases']
    assert holds == ['m/text/blocked', 'other/text/blocked']
    assert path.read_bytes() == before


@pytest.mark.parametrize('matches', [True, False])
def test_writable_host_review_releases_only_the_exact_failed_record(tmp_path, monkeypatch, matches):
    import pixelworld.audit as audit_module
    _, config, model = make_cases(tmp_path)
    run = run_path(config, model, 'text', 'blocked', 1)
    manifest = run / 'run_manifest.json'
    original = manifest.read_bytes()
    (config.runs_root/'audit_recovery_pending.json').write_text(json.dumps(
        {'schema_version': 1, 'cases': ['m/text/blocked']}))
    (config.runs_root/'audit_recovery_reviews.json').write_text(json.dumps({'records': {
        str(run.resolve()): {'eligible': False,
            'manifest_sha256': sha256(manifest) if matches else 'stale',
            'audit_sha256': sha256(Path(audit_module.__file__)),
            'recovery_tool_sha256': sha256(Path(revalidate_audit_failures.__file__))}}}))
    calls = []

    def read_only(run, *, apply):
        calls.append(run)
        raise OSError(30, 'Read-only file system')

    monkeypatch.setattr(revalidate_audit_failures, 'recover', read_only)
    report = watch_failed_retries.recover_audit_before_retry(config, True)
    assert bool(calls) is (not matches)
    assert bool(report['pending_writable_host']) is (not matches)
    assert bool(report['reviewed_unrecoverable']) is matches
    assert inspect(config, model, 'text', 'blocked').get('blocked_by_audit_revalidation', False) is (not matches)
    assert manifest.read_bytes() == original
    assert json.loads(manifest.read_text())['status'] == 'failed'
