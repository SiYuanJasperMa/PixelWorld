import json
from datetime import datetime, timezone

import pytest

from tools.ops.status import collect, cohort_status, local_processes


def fixture_cohort(tmp_path, counts=None):
    run_root = tmp_path/'runs'
    run_root.mkdir()
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'runs_root': str(run_root), 'case_ids': ['0001', '0002'],
        'models': [{'key': 'm', 'tracks': ['text']}]}))
    (run_root/'status.json').write_text(json.dumps({'supervisor_pid': 42,
        'updated_utc': '2026-11-27T00:00:00Z', 'groups': {'m/text': {
            'counts': counts or {'complete': 1, 'running': 1}}}}))
    return {'kind': 'analysis', 'benchmark': 'physics_iq', 'condition': 'physics_iq_text',
        'model_key': 'm', 'track': 'text', 'expected': 2, 'config': str(config), 'owner_role': 'cpu'}


def test_reboot_stale_running_count_does_not_claim_live_work(tmp_path):
    entry = fixture_cohort(tmp_path)
    original = (tmp_path/'runs/status.json').read_bytes()
    result = cohort_status(entry, role='cpu', processes={},
        now=datetime(2026, 11, 27, 1, tzinfo=timezone.utc))
    assert result['counts']['running'] == 1
    assert result['live_case_processes'] == 0
    assert result['dispatcher_live'] is False
    assert result['execution_state'] == 'no_owner_process_observed'
    assert result['snapshot_age_seconds'] == 3600
    assert (tmp_path/'runs/status.json').read_bytes() == original


def test_head_does_not_interpret_remote_worker_pid(tmp_path):
    entry = fixture_cohort(tmp_path)
    result = cohort_status(entry, role='head', processes={42: ['unrelated']})
    assert result['process_verification'] == 'requires_owner_host'
    assert result['live_case_processes'] is None
    assert result['dispatcher_live'] is None


def test_only_exact_model_track_scope_and_command_are_counted(tmp_path):
    entry = fixture_cohort(tmp_path)
    config = entry['config']
    command = ['python', '-m', 'pixelworld.cli', 'run-case', '--config', config,
        '--model', 'm', '--track', 'text', '--case', '0002']
    processes = {1: command, 2: [v if v != 'm' else 'other' for v in command],
        3: [v if v != 'text' else 'vision-agent' for v in command],
        4: [v if v != '0002' else 'outside' for v in command],
        42: ['python', '-m', 'pixelworld.cli', 'matrix', 'run', '--config', config]}
    result = cohort_status(entry, role='cpu', processes=processes)
    assert result['live_case_processes'] == 1
    assert result['dispatcher_live'] is True


def test_registered_legacy_inflight_source_is_counted_without_double_generation(tmp_path):
    entry = fixture_cohort(tmp_path)
    entry['compatible_configs'] = ['/registered/legacy.json']
    process = ['python', '-m', 'pixelworld.cli', 'run-case', '--config', '/registered/legacy.json',
        '--model', 'm', '--track', 'text', '--case', '0002']
    result = cohort_status(entry, role='cpu', processes={7: process})
    assert result['live_case_processes'] == 1
    assert result['execution_state'] == 'case_processes_live'


@pytest.mark.parametrize('counts', [{'complete': 3}, {'complete': True}, {'failed': 2}])
def test_invalid_counter_scope_is_unavailable_instead_of_completed(tmp_path, counts):
    entry = fixture_cohort(tmp_path, counts)
    result = cohort_status(entry, role='head')
    assert result['counts_basis'] == 'unavailable_or_invalid'
    assert result['counts']['complete'] == 0
    assert result['counts']['pending'] == 2
    assert str(tmp_path) not in json.dumps(result)


def test_exhausted_attempts_are_not_confirmed_model_failures(tmp_path):
    result = cohort_status(fixture_cohort(tmp_path, {'exhausted': 2}), role='cpu', processes={})
    assert result['retry_window_exhausted'] == 2
    assert result['confirmed_model_failures'] is None


def test_duplicate_cohort_does_not_inflate_totals(tmp_path):
    entry = fixture_cohort(tmp_path)
    with pytest.raises(ValueError, match='Duplicate cohort'):
        collect({'schema': 'pixelworld-operator-map-v1', 'cohorts': [entry, entry]})


def test_process_probe_ignores_zombie_and_reused_names(tmp_path):
    for pid, state in [(1, 'S'), (2, 'Z')]:
        proc = tmp_path/str(pid)
        proc.mkdir()
        (proc/'stat').write_text(f'{pid} (python process) {state} 0 0')
        (proc/'cmdline').write_bytes(b'python\0-m\0pixelworld.cli\0')
    assert local_processes(tmp_path) == {1: ['python', '-m', 'pixelworld.cli']}
