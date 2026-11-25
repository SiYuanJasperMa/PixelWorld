from pixelworld.publication_endpoints import CODE_REPOSITORY, canonicalize_code_links


def test_cached_legacy_templates_use_the_canonical_code_repository():
    old = 'https://github.com/SiyuanMaCS/PixelWorld'
    body = f'<a href="{old}/blob/main/README.md">code</a> {old}\n'
    result = canonicalize_code_links(body)
    assert old not in result
    assert CODE_REPOSITORY + '/blob/main/README.md' in result
    assert result.endswith(CODE_REPOSITORY + '\n')


def test_link_migration_preserves_other_projects_and_current_links():
    body = 'https://github.com/SiyuanMaCS/PixelWorld-extra ' + CODE_REPOSITORY
    assert canonicalize_code_links(body) == body
    assert canonicalize_code_links('http://github.com/siyuanmacs/pixelworld#usage') == CODE_REPOSITORY + '#usage'
    assert canonicalize_code_links('[Code](https://github.com/SiyuanMaCS/PixelWorld)') == '[Code](' + CODE_REPOSITORY + ')'


def test_legacy_frontend_cannot_replace_the_current_main_suite():
    from tools.watch_dashboard_publication import legacy_frontend_files
    result = legacy_frontend_files({'index.html': 'old main', 'physics_iq_baseline.html': 'old viewer',
        'analyses.html': '<a href="https://github.com/SiyuanMaCS/PixelWorld">code</a>', 'formal.js': 'legacy script'})
    assert 'index.html' not in result and 'physics_iq_baseline.html' not in result
    assert CODE_REPOSITORY in result['analyses.html'] and result['formal.js'] == 'legacy script'
