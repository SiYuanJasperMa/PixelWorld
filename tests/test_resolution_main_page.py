from tools.home_resolution_navigation import current_experiments, resolution_body


def test_home_promotes_720p_as_main_experiment():
    page=current_experiments()
    assert '720p / 24 FPS · 主实验' in page
    assert '共1,188条' in page
    assert '两档共2,376条' not in page
    assert '4K / 分辨率消融' in page


def test_resolution_page_has_main_first_information_hierarchy():
    page=resolution_body()
    required=[
        'resolution-main-overview',
        'resolution-main-summary',
        'resolution-main-matrix',
        'resolution-main-table',
        'resolution-latest-grid',
        'live-evaluation',
        'resolution-ablation',
        'resolution-ablation-4k',
        'resolution-token-time',
    ]
    assert all(f'id="{identifier}"' in page for identifier in required)
    assert page.index('id="resolution-main-matrix"') < page.index('id="resolution-latest"')
    assert page.index('id="resolution-latest"') < page.index('id="resolution-ablation"')
    assert '2,376 条全量视频' not in page
