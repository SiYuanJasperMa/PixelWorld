import json

import pytest

from pixelworld.util import sha256
from tools.run_flash_prompt_study import BASELINE_GUIDANCE, replace_guidance, smoke_gate


def test_prompt_replacement_preserves_task_and_scratch_instructions():
    original = 'Generate and save RGB.\n\n' + BASELINE_GUIDANCE + '\n\nUse .tmp/.\n'
    replacement = 'Save promptly; revise concrete defects; finish when complete.'
    updated = replace_guidance(original, replacement)
    assert updated.replace(replacement, BASELINE_GUIDANCE) == original
    with pytest.raises(ValueError):
        replace_guidance(original + BASELINE_GUIDANCE, replacement)


def gate_fixture(tmp_path):
    study = tmp_path / 'study'
    (study / 'tasks').mkdir(parents=True)
    (study / 'tasks/efficiency.md').write_text('Experimental efficiency prompt.')
    run = study / 'run'
    run.mkdir()
    baseline = study / 'baseline.json'
    common = {'case_id': '0043', 'codex_version': 'codex-cli 0.169.0',
              'initial_input_hashes': {'first_frame.png': 'image', 'artifact_io.py': 'helper'},
              'media_contract': {'fps': 24, 'frames': 120},
              'reasoning_effort': 'high', 'output_token_budget': 1000000}
    baseline.write_text(json.dumps(common))
    record = {**common, 'attempt': 1, 'status': 'complete', 'validation': {'pass': True},
              'token_usage': {'status': 'recorded'}, 'harness_variant': 'baseline',
              'flash_tools': None, 'initial_image_attached': True,
              'output_image_tool_enabled': True,
              'task_sha256': sha256(study / 'tasks/efficiency.md')}
    (run / 'run_manifest.json').write_text(json.dumps(record))
    (study / 'experiment_manifest.json').write_text(json.dumps(
        {'baseline_records': {'0043': str(baseline)}}))
    return study, run, record


def test_smoke_gate_requires_original_input_and_tool_boundary(tmp_path):
    study, run, record = gate_fixture(tmp_path)
    assert smoke_gate(study, run)['pass']
    record['initial_input_hashes']['artifact_io.py'] = 'changed helper'
    (run / 'run_manifest.json').write_text(json.dumps(record))
    assert not smoke_gate(study, run)['pass']


@pytest.mark.parametrize('field,value', [('output_image_tool_enabled', False),
    ('harness_variant', 'flash-submit'), ('output_token_budget', 100000),
    ('token_usage', {'status': 'partial'})])
def test_smoke_does_not_expand_with_changed_protocol_or_partial_usage(tmp_path, field, value):
    study, run, record = gate_fixture(tmp_path)
    record[field] = value
    (run / 'run_manifest.json').write_text(json.dumps(record))
    assert not smoke_gate(study, run)['pass']
