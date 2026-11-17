import json
import pytest
from tools.flash_context_proxy import workspace_for


def test_proxy_archives_only_in_registered_case_workspace(tmp_path):
    allowed=tmp_path/'experiment';workspace=allowed/'case/workspace';workspace.mkdir(parents=True)
    outside=tmp_path/'outside/workspace';outside.mkdir(parents=True)
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'abc':str(workspace),'def':str(outside)}))
    assert workspace_for('abc',registry,allowed)==workspace
    with pytest.raises(ValueError):workspace_for('def',registry,allowed)
    with pytest.raises(ValueError):workspace_for('../abc',registry,allowed)
    with pytest.raises(ValueError):workspace_for('123',registry,allowed)
