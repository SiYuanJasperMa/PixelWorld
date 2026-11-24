import json
from pathlib import Path

from tools import watch_rbench_appearance as watcher


MODELS = [f'm{i}' for i in range(8)]


def roster():
    return {m: (None, m) for m in MODELS}


def test_closed_requires_every_model_terminal():
    assert not watcher.closed({})
    assert not watcher.closed({'a': {'state': 'complete'}, 'b': {'state': 'pending'}})
    assert watcher.closed({'a': {'state': 'complete'}, 'b': {'state': 'exhausted'}})


def test_step_waits_until_every_case_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, 'inspect', lambda config, model, track, case: {'state': 'pending'})
    calls = []
    monkeypatch.setattr(watcher, 'evaluate', lambda *a: calls.append(a))

    result = watcher.step(roster(), tmp_path, object(), limit=4)

    assert calls == []
    assert result['generation_roster_closed'] is False
    assert result['finished'] is False
    assert len(result['cases']) == 50
    assert json.loads((tmp_path / 'execution.json').read_text())['expected_cases'] == 50


def test_step_scores_closed_case_and_records_generation_failure(tmp_path, monkeypatch):
    states = {m: {'state': 'complete', 'run': f'/runs/{m}'} for m in MODELS[:-1]}
    states[MODELS[-1]] = {'state': 'exhausted'}
    monkeypatch.setattr(watcher, 'inspect',
                        lambda config, model, track, case: states[model] if case == '0001' else {'state': 'pending'})
    monkeypatch.setattr(watcher, 'manifest_info',
                        lambda source, case, model: {'media_contract': {'frames': 120, 'fps': 24}})
    monkeypatch.setattr(watcher, 'sha256', lambda path: 'frozen-source')
    calls = []

    def fake_evaluate(client, benchmark, case, trial, videos, root):
        target = root / 'results' / benchmark / f'{case}_{trial}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{}')
        row = {'benchmark': benchmark, 'case': case, 'anonymous_mapping': {}, 'scores': [], 'pairs': []}
        calls.append((case, trial, [v['model'] for v in videos]))
        return row

    monkeypatch.setattr(watcher, 'evaluate', fake_evaluate)

    result = watcher.step(roster(), tmp_path, object(), limit=4)

    assert [c[1] for c in calls] == [0, 1]
    assert all(c[2] == MODELS[:-1] for c in calls)
    assert result['cases'][0]['state'] == 'scored'
    assert result['cases'][0]['scored_orders'] == 2
    assert result['cases'][0]['generation_failures'] == [MODELS[-1]]
    frozen = json.loads((tmp_path / 'case_inputs' / '0001.json').read_text())
    assert frozen['generation_failures'] == [MODELS[-1]]
    assert len(frozen['videos']) == 7


def test_step_freezes_inputs_and_requires_review_on_source_change(tmp_path, monkeypatch):
    states = {m: {'state': 'complete', 'run': f'/runs/{m}'} for m in MODELS}
    monkeypatch.setattr(watcher, 'inspect',
                        lambda config, model, track, case: states[model] if case == '0001' else {'state': 'pending'})
    monkeypatch.setattr(watcher, 'manifest_info',
                        lambda source, case, model: {'media_contract': {'frames': 120, 'fps': 24}})
    monkeypatch.setattr(watcher, 'sha256', lambda path: 'frozen-source')
    frozen = tmp_path / 'case_inputs' / '0001.json'
    frozen.parent.mkdir(parents=True)
    frozen.write_text('{}')
    calls = []
    monkeypatch.setattr(watcher, 'evaluate', lambda *a: calls.append(a))

    result = watcher.step(roster(), tmp_path, object(), limit=4)

    assert calls == []
    assert result['cases'][0]['state'] == 'source_changed_requires_review'
