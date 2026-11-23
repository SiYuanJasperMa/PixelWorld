import base64
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from pixelworld.controlled_harness import project
from pixelworld.messages_responses_proxy import messages_frontend
from pixelworld.token_budget import ResponseUsageBudget
from test_responses_budget_proxy import fake_provider, relay


def test_messages_frontend_shares_budget_and_preserves_image_and_tool_ids(tmp_path):
    img = base64.b64encode(b'fixture input bytes').decode()
    request = {'model':'pinned-model','max_tokens':100,'stream':True,
               'messages':[{'role':'user','content':[{'type':'image','source':{
                   'type':'base64','media_type':'image/png','data':img}}]}],
               'tools':[{'name':'mcp__pixelworld__read','input_schema':{'type':'object'}}]}
    reply = {'id':'resp_test','status':'completed','output':[{'type':'function_call',
             'call_id':'call_test','name':'mcp__pixelworld__read','arguments':'{"path":"prompt.txt"}'}],
             'usage':{'input_tokens':100,'output_tokens':75}}
    body = json.dumps(request).encode()
    budget = ResponseUsageBudget(64)
    with fake_provider([(200,'application/json',json.dumps(reply).encode())]) as (up, seen):
        with relay(up,tmp_path/'responses',budget) as url:
            with messages_frontend(responses_base=url,responses_key='test-relay',client_key='native-key',
                    model='pinned-model',effort='high',wire=tmp_path/'messages',
                    allowed_tools=frozenset({'mcp__pixelworld__read'})) as messages:
                req = Request(messages+'/v1/messages',data=body,headers={'x-api-key':'native-key'})
                with urlopen(req,timeout=5) as response:
                    data = response.read()
                assert b'call_test' in data and b'input_json_delta' in data
                assert (tmp_path/'messages/0001/input.json').read_bytes() == body
                assert (tmp_path/'messages/0001/response.raw').read_bytes() == data
                item = json.loads(seen[0])['input'][0]['content'][0]
                assert item['image_url'] == 'data:image/png;base64,' + img
                with pytest.raises(HTTPError) as blocked:
                    urlopen(req,timeout=5)
                assert blocked.value.code == 400 and len(seen) == 1
                assert json.load(blocked.value)['error']['message'] == 'output_token_budget_exhausted'


def test_projection_requires_native_terminal_and_rejects_stock_tools(tmp_path):
    part = {'tool':'pixelworld_bash','state':{'input':{'command':'python generate.py'},
            'status':'completed','output':'exit_code=0\nfinished'}}
    events = [{'type':'tool_use','part':part},{'type':'text','part':{'text':'done'}}]
    result, success, calls = project(events,'opencode',tmp_path,0)
    assert success and calls == 1 and result[-1] == {'type':'turn.completed'}
    assert not project(events,'opencode',tmp_path,124)[1]
    part['tool'] = 'bash'
    with pytest.raises(ValueError,match='outside the case'):
        project(events,'opencode',tmp_path,0)