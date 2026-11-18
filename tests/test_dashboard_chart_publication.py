import sys
from types import SimpleNamespace

from tools import watch_dashboard_publication as publisher


def test_frontend_publication_includes_svg_and_downloadable_png(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'; source = bundle / 'live_preview/pixelworld'
    source.mkdir(parents=True); (bundle / 'state').mkdir()
    (source / 'index.html').write_text('<body></body>')
    charts = source / 'charts'; charts.mkdir()
    (charts / 'pcve_scale.svg').write_text('<svg>updated score chart</svg>')
    (charts / 'pcve_scale.png').write_bytes(b'chart-fixture')
    operations = []

    def create_commit(*args, **kwargs):
        operations.extend(kwargs['operations'])
        return SimpleNamespace(oid='test')

    monkeypatch.setattr(publisher, 'SHELLS', ('index.html',))
    monkeypatch.setattr(publisher, 'validate_script_syntax', lambda _: None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(
        HfApi=lambda **kwargs: SimpleNamespace(create_commit=create_commit),
        CommitOperationAdd=lambda **kwargs: SimpleNamespace(**kwargs)))
    publisher.hf_frontend(source, None)
    assert {item.path_in_repo for item in operations} == {'index.html', 'charts/pcve_scale.svg', 'charts/pcve_scale.png'}
    assert b'updated score chart' in next(item.path_or_fileobj for item in operations if item.path_in_repo.endswith('.svg'))
