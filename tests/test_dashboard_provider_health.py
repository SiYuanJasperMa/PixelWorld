import json
import shutil
import subprocess

import pytest

from tools.build_formal_dashboard import JS


@pytest.mark.skipif(shutil.which('node') is None, reason='Node unavailable')
def test_model_api_indicator_uses_observed_model_and_keeps_unknown_empty():
    helper = JS[JS.index('function modelApi('):JS.index('function home(')]
    script = "const ago=()=> '2秒前';let cat={provider_health:{models:{'traex/native-model':{last_success_utc:'2026-11-19T00:00:00Z'}}}};"
    script += helper
    script += "console.log(JSON.stringify([modelApi({api_model:'native-model',label:'Display name'}),modelApi({label:'unknown'}),modelApi(null)]));"
    result = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert 'API 最近成功：2秒前' in result[0]
    assert result[1:] == ['', '']


@pytest.mark.skipif(shutil.which('node') is None, reason='Node unavailable')
def test_formal_dashboard_javascript_parses():
    subprocess.run(['node', '--check', '-'], input=JS, text=True, check=True,
                   capture_output=True)
