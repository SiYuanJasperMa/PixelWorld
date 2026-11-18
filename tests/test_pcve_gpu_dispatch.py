import json
from pathlib import Path
from types import SimpleNamespace

from tools import watch_pcve_gpu_evaluation as dispatch
from tools.run_pcve_gpu_worker import environment


def job(config, model='m', track='vision-agent'):
    return {'key': (str(config), model, track)}


def test_dispatch_recognizes_existing_legacy_and_new_evaluators(tmp_path):
    config = tmp_path / 'pcve_full.json'
    old = ['python', '/cloud/pcve_worker_start.py', 'm']
    new = ['python', '-m', 'tools.evaluate_pcve_full', '--config', str(config),
           '--model', 'm', '--track', 'vision-agent']
    probes = [{'worker': 'a', 'verified': True, 'commands': [[1, old], [2, new]]}]
    selected = dispatch.choose([job(config), job(config, 'next')], probes, config, 2)
    assert len(selected) == 1 and selected[0][1]['key'][1] == 'next'


def test_unknown_worker_is_not_assumed_stopped(tmp_path):
    config = tmp_path / 'config.json'
    assert dispatch.choose([job(config)], [{'worker': 'a', 'verified': False, 'commands': []}], config, 2) == []


def test_worker_capacity_and_main_priority(tmp_path):
    config = tmp_path / 'pcve_full.json'
    jobs = [job(config, 'main'), job(tmp_path / 'pcve_text_analysis.json', 'text', 'text')]
    probes = [{'worker': 'a', 'verified': True, 'commands': []}]
    selected = dispatch.choose(jobs, probes, config, 1)
    assert selected == [('a', jobs[0])]


def test_partial_generation_never_enters_evaluation_queue(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'; configs.mkdir()
    config_path = configs / 'pcve_full.json'; config_path.write_text('{}')
    runs = tmp_path / 'generations'; runs.mkdir()
    (runs / 'status.json').write_text(json.dumps({'groups': {'m/vision-agent': {'counts': {'complete': 128, 'exhausted': 1}}}}))
    config = SimpleNamespace(case_ids=tuple(range(129)), runs_root=runs,
                             models=[SimpleNamespace(key='m', tracks=('vision-agent',))])
    monkeypatch.setattr(dispatch, 'load_config', lambda _: config)
    assert dispatch.ready_jobs(tmp_path) == []
    (runs / 'status.json').write_text(json.dumps({'groups': {'m/vision-agent': {'counts': {'complete': 129}}}}))
    assert len(dispatch.ready_jobs(tmp_path)) == 1
    monkeypatch.setattr(dispatch, 'pcve_score', lambda *_: {'verified': True})
    assert dispatch.ready_jobs(tmp_path) == []


def test_gpu_worker_cache_and_workdirs_stay_in_cloud_bundle(tmp_path):
    code = tmp_path / 'code'; bundle = code / 'runs' / 'bundle'; output = bundle / 'evaluation' / 'm'
    env = environment(code, bundle, output)
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'TMPDIR', 'TORCH_HOME', 'MPLCONFIGDIR', 'XDG_CACHE_HOME', 'IMAGEIO_USERDIR'):
        assert Path(env[key]).is_relative_to(bundle)
    assert env['PYTHONPATH'].split(':')[0] == str(code)
