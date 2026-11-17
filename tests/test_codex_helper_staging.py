import json
import pytest
from tools.stage_codex_helpers import stage


def test_same_version_wrong_helper_is_rejected(tmp_path):
    package = tmp_path / 'package'
    (package / 'bin').mkdir(parents=True)
    (package / 'codex-resources').mkdir()
    binary = package / 'bin/codex'
    binary.write_bytes(b'fixture')
    (package / 'codex-package.json').write_text(json.dumps({
        'version': '0.169.0', 'resourcesDir': 'codex-resources'}))
    (package / 'codex-resources/bwrap').write_bytes(b'unrelated system helper')
    with pytest.raises(ValueError, match='pinned release hash'):
        stage(binary, tmp_path / 'runtime/bin')
    assert not (tmp_path / 'runtime/bin/bwrap').exists()
