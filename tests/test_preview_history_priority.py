import json
from pathlib import Path

from tools import live_benchmark_data as live
from tests.test_live_benchmark_data import make_run


def test_current_video_is_exported_without_waiting_for_historical_candidates(tmp_path, monkeypatch):
    run = make_run(tmp_path, status='complete')
    for index in range(3):
        path = run / 'trace' / f'round_{index:02d}'
        path.mkdir(parents=True)
        (path / 'snapshot.json').write_text(json.dumps({'utc': str(index)}))
    calls = []

    def export(source, destination, spec, label, utc):
        calls.append(source.name)
        return {'asset_id': source.name, 'pixels': None, 'label': label}

    monkeypatch.setattr(live, 'export_candidate', export)
    current = live.case_data(run, tmp_path / 'public', include_history=False)
    assert calls == ['workspace']
    assert current['candidate_history_pending'] and current['events']
    calls.clear()
    complete = live.case_data(run, tmp_path / 'public', previous=current, include_history=True)
    assert calls == ['round_00', 'round_01', 'round_02', 'workspace']
    assert not complete['candidate_history_pending']
    assert len(complete['candidates']) == 4


def test_native_streaming_does_not_take_packed_tensor_memory_lock(tmp_path, monkeypatch):
    source = tmp_path / 'workspace'; source.mkdir()
    (source / 'pixels.npz').write_bytes(b'saved tensor fixture')
    frames = source / 'frames'; frames.mkdir()
    for index in range(120):
        (frames / f'{index:02d}.png').touch()
    class RejectPackedLock:
        def __enter__(self):
            raise AssertionError('PNG streaming must not serialize behind packed tensors')
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(live, 'LARGE_MEDIA_RENDER_LOCK', RejectPackedLock())
    monkeypatch.setattr(live, 'large_media_headroom', lambda *args: True)
    monkeypatch.setattr(live, 'export_native_preview', lambda *args: {'preview_file': 'preview.mp4', 'pixels_file': None})
    result = live.export_candidate(source, tmp_path / 'public',
                                   {'width': 1280, 'height': 720, 'frames': 120, 'fps': 24}, 'final', None)
    assert result['preview_mp4'].endswith('preview.mp4')
