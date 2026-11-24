import json
from pathlib import Path

import pytest

from pixelworld.util import sha256
from tools.reclassify_sandbox_infra_failures import BACKUP, discover_manifests, eligible, reclassify

SINCE = '2026-11-24T09:20:00'


def write_run(root: Path, *, status='failed', frames=0, started='2026-11-24T10:00:00'):
    run = root / 'm/vision-agent/case_0001_attempt_01'
    (run / 'logs').mkdir(parents=True)
    binary = root / 'unrelated-runtime-name/bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'actual local executable')
    binary.chmod(0o755)
    (run / 'permissions_profile.json').write_text(json.dumps({'filesystem': {':minimal': 'read', '/usr/bin': 'read'}}))
    (run / 'run_manifest.json').write_text(json.dumps({
        'status': status, 'started_utc': started, 'case_id': '0001', 'attempt': 1,
        'model_key': 'm', 'track': 'vision-agent', 'validation': {'frames': frames, 'pass': False},
        'permissions_profile': {'sha256': sha256(run / 'permissions_profile.json')},
    }, separators=(',', ':')) + '\n\n')
    result = {'type': 'item.completed', 'item': {'id': 'command_1', 'type': 'command_execution',
              'command': 'pwd', 'status': 'failed', 'exit_code': 1,
              'aggregated_output': f'bwrap: execvp {binary}: No such file or directory\n'}}
    (run / 'logs/events.raw.jsonl').write_text(json.dumps(result) + '\n')
    return run, binary


def load(run):
    return json.loads((run / 'run_manifest.json').read_text())


def test_requires_native_error_not_zero_frames_and_root_name(tmp_path):
    run, _ = write_run(tmp_path)
    assert eligible(run, load(run), SINCE) == (True, 'recorded-hidden-runtime-execvp-error')
    raw = run / 'logs/events.raw.jsonl'
    error_text = json.loads(raw.read_text())['item']['aggregated_output']
    raw.write_text(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': error_text}}))
    assert eligible(run, load(run), SINCE) == (False, 'no-recorded-bwrap-execvp-error')


@pytest.mark.parametrize('change,reason', [
    ({'status': 'complete'}, 'not-failed'),
    ({'started_utc': '2026-11-20T00:00:00'}, 'before-window'),
    ({'validation': {'frames': 120}}, 'frames-present-or-unknown'),
    ({'validation': {'frames': False}}, 'frames-present-or-unknown'),
    ({'stop_reason': 'local_cli_configuration_failure'}, 'already-infra'),
    ({'permissions_profile': {'sha256': 'wrong'}}, 'unverified-profile'),
])
def test_rejects_nonmatching_attempts(tmp_path, change, reason):
    run, _ = write_run(tmp_path)
    manifest = load(run); manifest.update(change)
    assert eligible(run, manifest, SINCE) == (False, reason)


def test_actual_binary_visibility_overrides_runtime_substring(tmp_path):
    run, binary = write_run(tmp_path)
    profile = run / 'permissions_profile.json'
    profile.write_text(json.dumps({'filesystem': {str(binary.parent): 'read'}}))
    manifest = load(run); manifest['permissions_profile']['sha256'] = sha256(profile)
    assert eligible(run, manifest, SINCE) == (False, 'runtime-binary-visible')


def test_missing_host_binary_and_successful_command_are_ambiguous(tmp_path):
    run, binary = write_run(tmp_path)
    binary.unlink()
    assert eligible(run, load(run), SINCE) == (False, 'runtime-binary-unavailable')
    binary.write_bytes(b'executable'); binary.chmod(0o755)
    with (run / 'logs/events.raw.jsonl').open('a') as f:
        f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution',
                      'status': 'completed', 'exit_code': 0, 'aggregated_output': '/case'}}) + '\n')
    assert eligible(run, load(run), SINCE) == (False, 'successful-native-command-present')


def test_native_rollout_fallback_and_malformed_source(tmp_path):
    run, _ = write_run(tmp_path)
    raw = run / 'logs/events.raw.jsonl'
    item = json.loads(raw.read_text())['item']; item['type'] = 'CommandExecution'
    (run / 'logs/full_rollout.jsonl').write_text(json.dumps({'type': 'event_msg',
                     'payload': {'type': 'item_completed', 'item': item}}) + '\n')
    raw.unlink()
    assert eligible(run, load(run), SINCE)[0]
    with (run / 'logs/full_rollout.jsonl').open('a') as f: f.write('{broken\n')
    assert eligible(run, load(run), SINCE) == (False, 'malformed-native-command-results')


def test_apply_preserves_byte_exact_manifest_and_native_logs(tmp_path):
    run, _ = write_run(tmp_path)
    original = (run / 'run_manifest.json').read_bytes()
    raw = (run / 'logs/events.raw.jsonl').read_bytes()
    dry = reclassify(tmp_path, SINCE, apply=False)
    assert dry['eligible'] == 1 and not (run / BACKUP).exists()
    report = reclassify(tmp_path, SINCE, apply=True)
    assert (run / BACKUP).read_bytes() == original
    assert (run / 'logs/events.raw.jsonl').read_bytes() == raw
    manifest = load(run)
    assert manifest['status'] == 'failed' and manifest['validation']['frames'] == 0
    assert manifest['stop_reason'] == 'local_cli_configuration_failure'
    assert manifest['infra_reclassification']['original_manifest_sha256'] == report['items'][0]['sha256_before']
    assert reclassify(tmp_path, SINCE, apply=True)['eligible'] == 0


def test_conflicting_backup_prevents_any_manifest_change(tmp_path):
    run, _ = write_run(tmp_path)
    original = (run / 'run_manifest.json').read_bytes()
    (run / BACKUP).write_bytes(b'existing original evidence')
    with pytest.raises(ValueError, match='backup differs'):
        reclassify(tmp_path, SINCE, apply=True)
    assert (run / 'run_manifest.json').read_bytes() == original


def test_discovery_never_descends_into_case_workspace(tmp_path, monkeypatch):
    run, _ = write_run(tmp_path)
    fake = run / 'workspace/m/vision-agent/case_hidden'
    fake.mkdir(parents=True); (fake / 'run_manifest.json').write_text('{}')
    monkeypatch.setattr(Path, 'rglob', lambda *args, **kwargs: pytest.fail('recursive scan'))
    assert discover_manifests(tmp_path) == [run / 'run_manifest.json']
