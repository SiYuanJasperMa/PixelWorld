import json
import pytest
from tools.flash_context_proxy import workspace_for, core_tools, CORE_TOOLS


def test_proxy_archives_only_in_registered_case_workspace(tmp_path):
    allowed=tmp_path/'experiment';workspace=allowed/'case/workspace';workspace.mkdir(parents=True)
    outside=tmp_path/'outside/workspace';outside.mkdir(parents=True)
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'abc':str(workspace),'def':str(outside)}))
    assert workspace_for('abc',registry,allowed)==workspace
    with pytest.raises(ValueError):workspace_for('def',registry,allowed)
    with pytest.raises(ValueError):workspace_for('../abc',registry,allowed)
    with pytest.raises(ValueError):workspace_for('123',registry,allowed)


def test_schema_intervention_preserves_all_pixel_tools_and_exact_input(tmp_path):
    tools=[{'type':'function','name':name,'parameters':{'type':'object'}} for name in sorted(CORE_TOOLS)]
    tools += [{'type':'function','name':'get_goal'},{'type':'function','name':'request_user_input'}]
    request={'input':[{'role':'user','content':'original task'}],'tools':tools,'tool_choice':'auto'}
    changed,report=core_tools(request,tmp_path)
    assert changed['input'] is request['input'] and changed['tool_choice']=='auto'
    assert {t['name']for t in changed['tools']}==CORE_TOOLS
    assert request['tools']==tools
    assert report['removed_tools']==['get_goal','request_user_input']
    archive=next((tmp_path/'.tmp/harness-history').glob('tool-schemas-*.json'))
    assert json.loads(archive.read_text())==tools


def test_schema_intervention_cannot_remove_a_required_pixel_tool(tmp_path):
    with pytest.raises(ValueError,match='complete pixel-task'):
        core_tools({'tools':[{'name':'exec_command'}]},tmp_path)
