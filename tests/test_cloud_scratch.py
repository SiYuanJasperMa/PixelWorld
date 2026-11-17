from pathlib import Path

from pixelworld.config import Model, Provider
from pixelworld.runner import codex_command
from pixelworld.runner import _execution_infra_failure
import json


def test_cloud_workspace_owns_library_caches_and_scratch():
    workspace = Path('/mnt/bn/test-pixelworld/run/workspace')
    model = Model(key='test', model='test', reasoning_effort='high',
                  provider=Provider(type='builtin'), tracks=('text',), cli_backend='codex')
    command = codex_command(model, 'text', workspace)
    values = [command[i+1] for i, value in enumerate(command[:-1]) if value == '-c']
    settings = dict(value.split('=', 1) for value in values if '=' in value)
    for key in ('TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'MPLCONFIGDIR',
                'NUMBA_CACHE_DIR', 'HF_HOME', 'PYTHONPYCACHEPREFIX'):
        assert settings['shell_environment_policy.set.' + key].strip('"').startswith(str(workspace / '.tmp'))
    assert 'shell_environment_policy.inherit=none' in values
    assert settings['shell_environment_policy.set.OPENBLAS_NUM_THREADS'] == '"1"'
    assert '--disable' in command and 'view_image' in command


def test_native_sandbox_failure_is_infrastructure(tmp_path):
    item = {'type': 'function_call_output', 'call_id': 'call1',
            'output': "Process exited with code 101\nlinux-sandbox/src/launcher.rs:60\nbubblewrap is unavailable"}
    (tmp_path / 'full_rollout.jsonl').write_text(json.dumps({'type': 'response_item', 'payload': item})+'\n')
    assert _execution_infra_failure(tmp_path) == 'sandbox_dependency_missing'
    # The same words in a model answer are not trusted infrastructure evidence.
    item['type'] = 'message'
    (tmp_path / 'full_rollout.jsonl').write_text(json.dumps({'type': 'response_item', 'payload': item})+'\n')
    assert _execution_infra_failure(tmp_path) is None
