import pytest

from tools.build_main_suite_status import (
    JUDGE, legacy_score, phygen_score, public_counts, rbench_score,
)


def test_rbench_never_displays_a_partial_or_failed_cohort_mean():
    row = {'scored': 49, 'expected': 50, 'full_common50': True, 'mean': 0.71}
    report = {'protocol': 'rbench-common-official-gpt-branch-gpt61-sol-v1',
              'judge': JUDGE, 'models': {'example': row}}
    assert rbench_score(report, 'example') == (49, None)
    row.update(scored=50, judge_errors=['0049'])
    assert rbench_score(report, 'example') == (50, None)
    row['judge_errors'] = []
    assert rbench_score(report, 'example') == (50, 0.71)
    report['judge'] = 'legacy'
    assert rbench_score(report, 'example') == (0, None)


def test_phygen_requires_all_160_cases_with_the_new_judge():
    row = {'cases': 159, 'mean': 0.73}
    report = {'version': 'phygen-gpt61-sol-three-stage-mcq-v1', 'judge': JUDGE,
              'full160': True, 'cases': list(range(1, 161)), 'models': {'example': row}}
    assert phygen_score(report, 'example') == (159, None)
    row['cases'] = 160
    assert phygen_score(report, 'example') == (160, 0.73)
    report['cases'][2] = 2
    assert phygen_score(report, 'example') == (0, None)


def test_pes_requires_full129_and_keeps_negative_or_zero_values():
    state = {'full_scope_scored': True, 'score': {'scope': 99, 'metrics': {'PES': -0.1}}}
    assert legacy_score(state, 129, 'PES') is None
    state['score']['scope'] = 129
    assert legacy_score(state, 129, 'PES') == -0.1
    state['score']['metrics']['PES'] = 0.
    assert legacy_score(state, 129, 'PES') == 0.
    with pytest.raises(ValueError):
        public_counts({'counts': {'complete': 129, 'pending': 1}}, 129)
