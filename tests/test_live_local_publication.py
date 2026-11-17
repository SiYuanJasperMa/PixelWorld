import json
import sys
from tools import live_benchmark_data as live


def test_live_refresh_succeeds_without_git_or_remote_upload(tmp_path, monkeypatch):
    checkout = tmp_path / 'website'
    source = tmp_path / 'runs'
    source.mkdir()

    def update(_source, destination, *_args):
        live.save(destination / 'catalogue.json', {'groups': [], 'updated_utc': 'now'})

    def reject_git(*_args, **_kwargs):
        raise AssertionError('Local refresh must not depend on Git')

    monkeypatch.setattr(live, 'async_update', update)
    monkeypatch.setattr(live, 'collect_evaluation', lambda _: {})
    monkeypatch.setattr(live.subprocess, 'check_output', reject_git)
    monkeypatch.setattr(live.subprocess, 'run', reject_git)
    monkeypatch.setattr(sys, 'argv', ['live', '--run-root', str(source),
        '--checkout', str(checkout), '--local-only', '--once'])
    live.main()
    status = json.loads((checkout / '.publisher_status.json').read_text())
    assert status['status'] == 'ok'
    assert status['mode'] == 'local-only'
    assert json.loads((checkout / 'pixelworld/catalogue.json').read_text())['updated_utc'] == 'now'
