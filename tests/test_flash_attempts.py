import json

from tools.flash_attempts import accepted, histories, outcome, selected


def test_infrastructure_retry_preserves_history_and_selects_first_success(tmp_path):
    original = {'case_id': '0001', 'attempt': 1, 'status': 'failed',
                'stop_reason': 'storage_infrastructure_failure', 'validation': {'infra_error': 'storage'}}
    rows = [original, {'case_id': '0001', 'attempt': 2, 'status': 'complete', 'validation': {'pass': True}},
            {'case_id': '0001', 'attempt': 3, 'status': 'complete', 'validation': {'pass': True}}]
    for row in rows:
        run = tmp_path / f"case_0001_attempt_{row['attempt']:02d}"
        run.mkdir()
        (run / 'run_manifest.json').write_text(json.dumps(row))
    found = histories(tmp_path)['0001']
    assert len(found) == 3 and found[0][1] == original
    assert selected(found)[1]['attempt'] == 2 and outcome(found) == 'complete'
    assert outcome(found[:1]) == 'stale'
    assert not accepted(original)


def test_running_retry_takes_precedence_without_hiding_real_failures():
    rows = [(None, {'status': 'failed', 'validation': {'infra_error': 'storage'}}),
            (None, {'status': 'failed', 'stop_reason': 'output_token_budget_exhausted'})]
    assert outcome(rows) == 'failed'
    assert selected(rows)[1]['stop_reason'] == 'output_token_budget_exhausted'
    rows.append((None, {'status': 'running'}))
    assert outcome(rows) == 'running'
