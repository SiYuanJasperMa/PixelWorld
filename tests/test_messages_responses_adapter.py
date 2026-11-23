import base64
import json

import pytest

from pixelworld.messages_responses_adapter import to_messages, to_responses

MODEL='test-model'


def test_messages_adapter_preserves_image_tool_call_and_tool_result_order():
    img={'type':'image','source':{'type':'base64','media_type':'image/png','data':base64.b64encode(b'original bytes').decode()}}
    payload={'model':MODEL,'max_tokens':32768,'system':'native system prompt','messages':[
        {'role':'user','content':[{'type':'text','text':'Read a frame'},img]},
        {'role':'assistant','content':[{'type':'text','text':'Inspecting'}, {'type':'tool_use','id':'call_1','name':'view_image','input':{'path':'preview.png'}}]},
        {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_1','content':[img]}]},
    ],'tools':[{'name':'view_image','input_schema':{'type':'object'}}],'tool_choice':{'type':'auto','disable_parallel_tool_use':True}}
    r=to_responses(payload,model=MODEL,reasoning_effort='high')
    assert [x.get('type',x.get('role'))for x in r['input']]==['system','user','assistant','function_call','function_call_output']
    assert r['input'][-1]['output']==[r['input'][1]['content'][1]]
    assert json.loads(r['input'][-2]['arguments'])=={'path':'preview.png'}
    assert r['parallel_tool_calls'] is False and r['max_output_tokens']==32768


def test_messages_adapter_refuses_model_substitution_and_remote_image():
    p={'model':'different','max_tokens':20,'messages':[]}
    with pytest.raises(ValueError, match='different model'):
        to_responses(p,model=MODEL,reasoning_effort='high')
    p['model']=MODEL;p['messages']=[{'role':'user','content':[{'type':'image','source':{'type':'url','url':'https://example.test/a.png'}}]}]
    with pytest.raises(ValueError,match='embedded'):
        to_responses(p,model=MODEL,reasoning_effort='high')


def test_response_adapter_preserves_tool_identity_and_reported_usage():
    d={'id':'resp_1','status':'completed','output':[{'type':'function_call','name':'write','call_id':'call_1','arguments':'{"path":"x"}'}],
       'usage':{'input_tokens':24,'output_tokens':3}}
    r=to_messages(d,model=MODEL)
    assert r['stop_reason']=='tool_use' and r['content']==[{'type':'tool_use','name':'write','id':'call_1','input':{'path':'x'}}]
    assert r['usage']=={'input_tokens':24,'output_tokens':3}


def test_locked_native_effort_and_reasoning_projection():
    p = {'model': MODEL, 'max_tokens': 32000, 'messages': [],
         'thinking': {'type': 'adaptive', 'display': 'updates'},
         'output_config': {'effort': 'high'},
         'context_management': {'edits': [{'type': 'clear_thinking_20251015', 'keep': 'all'}]}}
    assert to_responses(p, model=MODEL, reasoning_effort='high')['reasoning'] == {'effort': 'high'}
    with pytest.raises(ValueError, match='locked effort'):
        to_responses(p, model=MODEL, reasoning_effort='low')
    d = {'id': 'resp_1', 'status': 'completed', 'usage': {'input_tokens': 24, 'output_tokens': 3},
         'output': [{'type': 'reasoning', 'id': 'rs_1', 'summary': []},
                    {'type': 'message', 'content': [{'type': 'output_text', 'text': 'DONE'}]}]}
    assert to_messages(d, model=MODEL)['content'] == [{'type': 'text', 'text': 'DONE'}]
