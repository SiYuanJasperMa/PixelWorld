import json
from pathlib import Path

from tools.reclassify_sandbox_infra_failures import eligible, reclassify

SINCE = '2026-11-24T09:20:00'


def write_run(root: Path, *, status='failed', frames=0, roots=('/usr/bin',), started='2026-11-24T10:00:00'):
    run = root / 'case_0001_attempt_01'
    run.mkdir(parents=True)
    (run / 'run_manifest.json').write_text(json.dumps({
        'status': status, 'started_utc': started, 'case_id': '0001', 'attempt': 1,
        'model_key': 'm', 'validation': {'frames': frames, 'pass': False},
    }))
    (run / 'permissions_profile.json').write_text(json.dumps({
        'filesystem': {path: 'read' for path in roots},
    }))
    return run


def test_only_zero_frame_attempts_without_runtime_roots_are_eligible(tmp_path):
    bad = write_run(tmp_path / 'bad')
    good = write_run(tmp_path / 'good', roots=('/usr/bin', '/runtime/runtime_main_worker_20261116/bin'))
    complete = write_run(tmp_path / 'complete', status='complete', frames=120)
    old = write_run(tmp_path / 'old', started='2026-11-20T00:00:00')

    assert eligible(bad, json.loads((bad / 'run_manifest.json').read_text()), SINCE) == (True, 'missing-runtime-roots')
    assert eligible(good, json.loads((good / 'run_manifest.json').read_text()), SINCE) == (False, 'runtime-rooted')
    assert eligible(complete, json.loads((complete / 'run_manifest.json').read_text()), SINCE) == (False, 'not-failed')
    assert eligible(old, json.loads((old / 'run_manifest.json').read_text()), SINCE) == (False, 'before-window')


def test_apply_preserves_evidence_and_marks_infra(tmp_path):
    run = write_run(tmp_path / 'run')
    report = reclassify(tmp_path, SINCE, apply=True)
    assert report['eligible'] == 1
    manifest = json.loads((run / 'run_manifest.json').read_text())
    assert manifest['status'] == 'failed'
    assert manifest['stop_reason'] == 'local_cli_configuration_failure'
    assert manifest['validation']['infra_error'] == 'sandbox_profile_missing_runtime_roots'
    assert manifest['validation']['frames'] == 0
    assert manifest['infra_reclassification']['original_stop_reason'] is None
    assert report['items'][0]['sha256_before'] != report['items'][0]['sha256_after']
