import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import replace

from PIL import Image

from pixelworld.config import load_config
from pixelworld.runner import run_case, run_path, _seed_provider_env
from pixelworld.supervisor import inspect, launch_pid_path


def prepared(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'inputs_root':'inputs','runs_root':'runs','case_ids':['one'],
                               'models':[{'key':'m','model':'fixture','tracks':['text']}]}))
    config = load_config(path)
    source = config.inputs_root / 'one'
    source.mkdir(parents=True)
    Image.new('RGB', (32, 24), 'white').save(source / 'first_frame.png')
    (source / 'prompt.txt').write_text('Fixture input only.')
    (source / 'task_spec.json').write_text(json.dumps(
        {'task_type':'i2v','width':32,'height':24,'frames':6,'fps':3,'audio':None}))
    return config, config.models[0]


def test_live_launch_blocks_retry_before_run_directory_exists(tmp_path):
    config, model = prepared(tmp_path)
    args = ['-m','pixelworld.cli','run-case','--config',str(config.source),
            '--model','m','--track','text','--case','one','--attempt','1']
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', *args])
    try:
        owner = launch_pid_path(config, model, 'text', 'one')
        owner.parent.mkdir(parents=True)
        owner.write_text(str(child.pid))
        assert not run_path(config, model, 'text', 'one', 1).exists()
        assert inspect(config, model, 'text', 'one')['state'] == 'running'
        run = run_path(config, model, 'text', 'one', 1)
        run.mkdir(parents=True)
        (run/'run_manifest.json').write_text(json.dumps({'status':'complete','validation':{'pass':True}}))
        assert inspect(config, model, 'text', 'one')['state'] == 'complete'
    finally:
        child.terminate()
        child.wait(timeout=10)
    (run/'run_manifest.json').unlink()
    assert inspect(config, model, 'text', 'one')['state'] != 'running'


def test_concurrent_attempts_cannot_overwrite_the_same_run(tmp_path, monkeypatch):
    config, model = prepared(tmp_path)
    run = run_path(config, model, 'text', 'one', 1)
    original_exists = Path.exists
    barrier = threading.Barrier(2)

    def exists(path):
        result = original_exists(path)
        if path == run and not result:
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(Path, 'exists', exists)

    def launch():
        try:
            return run_case(config, 'm', 'text', 'one', codex_binary='/bin/true', dry_run=True)
        except FileExistsError:
            return 'already_owned'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: launch(), range(2)))
    assert results.count('already_owned') == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert json.loads((run/'run_manifest.json').read_text())['case_id'] == 'one'


def test_loopback_provider_bypasses_proxy_even_with_existing_api_key(tmp_path):
    config, model = prepared(tmp_path)
    model = replace(model, provider=replace(model.provider, base_url='http://127.0.0.1:9714/v1', env_key='PIXELWORLD_API_KEY'))
    environment = {'HTTP_PROXY':'http://proxy.example:8080','PIXELWORLD_API_KEY':'already-present',
                   'NO_PROXY':'example.org','no_proxy':'internal.example.net'}
    _seed_provider_env(environment, config, model)
    assert set(environment['NO_PROXY'].split(',')) == {'127.0.0.1','localhost','::1','example.org','internal.example.net'}
    assert environment['no_proxy'] == environment['NO_PROXY']
    assert environment['HTTP_PROXY'] == 'http://proxy.example:8080'
    assert environment['PIXELWORLD_API_KEY'] == 'already-present'


def test_ipv6_loopback_provider_bypasses_proxy(tmp_path):
    config, model = prepared(tmp_path)
    model = replace(model, provider=replace(model.provider, base_url='http://[::1]:9714/v1'))
    environment = {'HTTPS_PROXY':'http://proxy.example:8080'}
    _seed_provider_env(environment, config, model)
    assert '::1' in environment['NO_PROXY'].split(',')


def test_external_provider_keeps_existing_proxy_routing(tmp_path):
    config, model = prepared(tmp_path)
    model = replace(model, provider=replace(model.provider, base_url='https://api.example.com/v1'))
    environment = {'HTTP_PROXY':'http://proxy.example:8080','NO_PROXY':'example.org'}
    before = dict(environment)
    _seed_provider_env(environment, config, model)
    assert environment == before
