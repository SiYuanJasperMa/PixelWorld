import json

import pytest

from tools.build_registered_cost_snapshot import estimate, recorded_usage


TARIFF = {'input': 2, 'cached_input': .1, 'output': 10,
          'source': 'https://example.org/official-prices',
          'long_context_input_tokens': 272000,
          'long_context_input_multiplier': 2, 'long_context_output_multiplier': 1.5}


def make_run(tmp_path, usage, requests=()):
    (tmp_path / 'recording').mkdir()
    (tmp_path / 'run_manifest.json').write_text(json.dumps({'token_usage': usage}))
    (tmp_path / 'recording/api_usage.jsonl').write_text(''.join(
        json.dumps({'record_kind': 'api_request_usage', 'response_id': rid, 'usage': value}) + '\n'
        for rid, value in requests))
    return tmp_path


def test_request_premium_depends_on_each_request_not_run_total(tmp_path):
    usage = {'input_tokens': 500000, 'output_tokens': 10000, 'reasoning_output_tokens': 8000}
    requests = [{'input_tokens': 200000, 'output_tokens': 4000},
                {'input_tokens': 300000, 'output_tokens': 6000}]
    run = make_run(tmp_path, usage, [('resp_a', requests[0]), ('resp_b', requests[1])])
    measured, ledger = recorded_usage(run)
    cost, audit, premium = estimate(measured, ledger, TARIFF, .7)
    assert cost == pytest.approx(.2 * .67 + .04 + .3 * .67 * 2 + .06 * 1.5)
    assert premium == 1 and audit.startswith('Per-request')


def test_mismatched_request_ledger_cannot_authorize_long_context_premium(tmp_path):
    usage = {'input_tokens': 600000, 'output_tokens': 10000}
    run = make_run(tmp_path, usage, [('resp_a', {'input_tokens': 300000, 'output_tokens': 10000})])
    measured, ledger = recorded_usage(run)
    cost, audit, premium = estimate(measured, ledger, TARIFF, .7)
    assert ledger == [] and premium == 0
    assert cost == pytest.approx(.6 * .67 + .1)
    assert audit.startswith('Base-rate estimate')


@pytest.mark.parametrize('usage', [
    {'input_tokens': None, 'output_tokens': 100},
    {'input_tokens': 100, 'output_tokens': None},
    {'input_tokens': True, 'output_tokens': 100},
    {'status': 'partial', 'input_tokens': 100, 'output_tokens': 100},
])
def test_unknown_or_partial_usage_is_not_zero(tmp_path, usage):
    assert recorded_usage(make_run(tmp_path, usage)) == (None, [])


def test_duplicate_request_receipts_count_once(tmp_path):
    usage = {'input_tokens': 500, 'output_tokens': 30}
    run = make_run(tmp_path, usage, [('resp_a', usage), ('resp_a', usage)])
    measured, ledger = recorded_usage(run)
    assert measured == usage and ledger == [usage]


def test_conflicting_duplicate_request_receipts_disable_premium(tmp_path):
    usage = {'input_tokens': 500, 'output_tokens': 30}
    run = make_run(tmp_path, usage, [('resp_a', usage), ('resp_a', {**usage, 'output_tokens': 31})])
    assert recorded_usage(run) == (usage, [])


def test_missing_official_tariff_does_not_substitute_another_model_price():
    cost, audit, premium = estimate({'input_tokens': 500, 'output_tokens': 30}, [], None, .7)
    assert cost is None and premium == 0 and 'unavailable' in audit


def test_phygen_plot_uses_current_full_scope_judge_score():
    from tools.build_cost_score_plots import collect
    row = {'benchmark': 'phygenbench', 'model': 'gpt62-sol', 'condition': 'phygenbench_text',
           'complete': 160, 'expected': 160, 'standardized_cache70_cost_usd': .8,
           'usage_missing_cases': 0, 'metrics': {'Overall': .99}}
    analysis = {'conditions': [{'id': 'phygenbench_text', 'models': [{
        'key': 'gpt62-sol', 'complete': 160, 'evaluation': {
            'full_scope_scored': True, 'metrics': {'Overall': .7145833333}}}]}]}
    rows, excluded = collect({'rows': [row]}, {}, analysis)
    assert not excluded and rows[0]['score'] == pytest.approx(.7145833333)
    assert rows[0]['score_source'] == 'current_analysis_scope'


def test_incomplete_current_analysis_cannot_fall_back_to_legacy_score():
    from tools.build_cost_score_plots import collect
    row = {'benchmark': 'physics_iq', 'model': 'gpt62-sol', 'condition': 'physics_iq_fps48',
           'complete': 198, 'expected': 198, 'standardized_cache70_cost_usd': .8,
           'usage_missing_cases': 0, 'metrics': {'Verified': 99}}
    analysis = {'conditions': [{'id': 'physics_iq_fps48', 'models': [{
        'key': 'gpt62-sol', 'complete': 198, 'evaluation': {'full_scope_scored': False}}]}]}
    rows, excluded = collect({'rows': [row]}, {}, analysis)
    assert not rows and excluded[0]['reason'] == 'current full-scope analysis score pending'
