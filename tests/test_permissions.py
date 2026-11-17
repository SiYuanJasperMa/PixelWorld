import json
from pathlib import Path

import pytest

from pixelworld.config import Model, Provider, load_config
from pixelworld.permissions import case_permissions, permission_args
from pixelworld.runner import codex_command, run_case


def test_case_policy_exposes_runtime_libraries_but_not_neighbor_runs(monkeypatch, tmp_path):
    from pixelworld import permissions
    runtime = tmp_path / 'runtime'
    workspace = tmp_path / 'runs/case/workspace'
    monkeypatch.setattr(permissions.sys, 'executable', str(runtime / 'bin/python'))
    monkeypatch.setattr(permissions.sysconfig, 'get_path', lambda name: str(runtime / 'lib/python3.11'))
    monkeypatch.setattr(permissions.shutil, 'which', lambda name: str(runtime / 'bin' / name))
    profile = case_permissions(workspace)
    entries = profile['filesystem']
    assert entries[str(workspace)] == 'write'
    assert entries[':minimal'] == 'read'
    assert entries[str(runtime / 'bin')] == 'read'
    assert entries[str(runtime / 'lib/python3.11')] == 'read'
    assert str(runtime) not in entries
    assert str(workspace.parent) not in entries
    assert '/' not in entries and ':root' not in entries
    assert '/dev' not in entries and '/dev/null' not in entries
    assert profile['network']['enabled'] is False


def test_case_policy_uses_exact_inline_table_keys(tmp_path):
    import tomllib
    workspace = tmp_path / 'case.with.dots/workspace'
    args = permission_args(case_permissions(workspace))
    value = next(x.split('=', 1)[1] for x in args if x.startswith('permissions.pixelworld_case.filesystem='))
    parsed = tomllib.loads('filesystem=' + value)['filesystem']
    assert parsed[str(workspace)] == 'write'
    assert ':root' not in parsed


def test_restricted_command_preserves_text_and_vision_tools(tmp_path):
    model = Model('test', 'test', 'high', Provider(), ('text', 'vision-agent'))
    for track in model.tracks:
        command = codex_command(model, track, tmp_path / 'workspace', filesystem_policy='case-only-v1')
        assert '--sandbox' not in command
        assert 'default_permissions="pixelworld_case"' in command
        assert 'permissions.pixelworld_case.network.enabled=false' in command
        assert ('--image' in command) == (track == 'vision-agent')
        assert ('view_image' in command) == (track == 'text')


def test_config_requires_native_nonlegacy_tracks_for_case_isolation(tmp_path):
    raw = {'inputs_root': 'inputs', 'runs_root': 'runs', 'filesystem_policy': 'case-only-v1',
           'models': [{'key': 'test', 'model': 'test', 'tracks': ['text']}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw))
    assert load_config(path).filesystem_policy == 'case-only-v1'
    raw['models'][0]['tracks'] = ['vision-input']
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='Text/Vision'):
        load_config(path)
    raw['models'][0]['tracks'] = ['text']
    raw['protocol'] = 'pixelworld-v1'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='Legacy'):
        load_config(path)


def test_attempt_records_the_effective_folder_policy(tmp_path):
    from PIL import Image
    raw = {'inputs_root': 'inputs', 'runs_root': 'runs', 'filesystem_policy': 'case-only-v1',
           'models': [{'key': 'test', 'model': 'test', 'tracks': ['text']}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw))
    source = tmp_path / 'inputs/0001'
    source.mkdir(parents=True)
    Image.new('RGB', (256, 144)).save(source / 'first_frame.png')
    (source / 'prompt.txt').write_text('Audit fixture.')
    manifest = run_case(load_config(path), 'test', 'text', '0001', dry_run=True)
    run = tmp_path / 'runs/test/text/case_0001_attempt_01'
    assert manifest['filesystem_policy'] == 'case-only-v1'
    assert manifest['permissions_profile']['sha256']
    profile = json.loads((run / 'permissions_profile.json').read_text())
    assert profile['filesystem'][str(run / 'workspace')] == 'write'
    assert str(run) not in profile['filesystem']


def test_reuse_does_not_relabel_old_results_as_read_isolated(tmp_path):
    import hashlib
    from PIL import Image
    from pixelworld.media import contract, task_rules
    from tools.audit_reuse import audit
    raw = {'inputs_root': 'inputs', 'runs_root': 'runs', 'filesystem_policy': 'case-only-v1',
           'models': [{'key': 'test', 'model': 'test', 'tracks': ['text']}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw))
    source = tmp_path / 'inputs/0001'
    source.mkdir(parents=True)
    Image.new('RGB', (256, 144)).save(source / 'first_frame.png')
    (source / 'prompt.txt').write_text('Fixture prompt.')
    spec = contract(source)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {'status': 'complete', 'validation': {'pass': True}, 'model': 'test',
                'generation_contract': 'minimal-v2',
                'track': 'text', 'media_contract': spec, 'codex_version': 'codex-cli 0.169.0',
                'reasoning_effort': 'high', 'output_token_budget': 100000,
                'input_image_sha256': digest(source / 'first_frame.png'),
                'input_prompt_sha256': digest(source / 'prompt.txt'),
                'rules_sha256': hashlib.sha256(task_rules(spec).encode()).hexdigest()}
    previous = tmp_path / 'previous/test/text/case_0001_attempt_01'
    previous.mkdir(parents=True)
    for name in ['generate.py', 'pixels.npz', 'inspection.png', 'transition_check.png',
                 'index.html', 'frames/00.png', 'frames/19.png']:
        artifact = previous / 'workspace' / name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b'existing delivery')
    record = previous / 'run_manifest.json'
    record.write_text(json.dumps(manifest))
    report = audit(path, tmp_path / 'previous')
    assert report['eligible'] == []
    assert report['exclusion_checks']['filesystem_policy'] == 1
    manifest['filesystem_policy'] = 'case-only-v1'
    record.write_text(json.dumps(manifest))
    assert len(audit(path, tmp_path / 'previous')['eligible']) == 1
