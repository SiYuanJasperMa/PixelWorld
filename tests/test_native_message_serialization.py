import json

from pixelworld import traex_responses_proxy as proxy


def request(monkeypatch, items):
    monkeypatch.setattr(proxy,'MODELS',{'test':'native-test'})
    monkeypatch.setattr(proxy,'MODEL_CONFIG',{'test':{'native_model_name':'native-test'}})
    return proxy.native_request({'model':'test','input':items})['messages']


def call(name, identity):
    return {'type':'function_call','name':name,'call_id':identity,'arguments':'{"value":7}'}


def output(identity, value):
    return {'type':'function_call_output','call_id':identity,'output':value}


def test_parallel_calls_share_assistant_message_and_keep_result_ids(monkeypatch):
    messages=request(monkeypatch,[{'role':'user','content':'test'},call('a','callA'),
        call('b','callB'),output('callB','eleven'),output('callA','seven')])
    assert [m['role'] for m in messages]==['user','assistant','tool','tool']
    assert [(c['index'],c['id'],c['function_call']['name']) for c in messages[1]['tool_calls']]==[
        (0,'callA','a'),(1,'callB','b')]
    assert [m['tool_call_id'] for m in messages[2:]]==['callB','callA']


def test_sequential_batches_are_not_merged(monkeypatch):
    messages=request(monkeypatch,[call('a','callA'),output('callA','seven'),
        call('b','callB'),output('callB','eleven')])
    assert [m['role'] for m in messages]==['assistant','tool','assistant','tool']
    assert [m['tool_calls'][0]['index'] for m in messages if m['role']=='assistant']==[0,0]


def test_wrapped_custom_input_and_function_arguments_stay_exact(monkeypatch):
    raw='*** Begin Patch\n*** End Patch'
    messages=request(monkeypatch,[{'type':'custom_tool_call','name':'apply_patch',
        'call_id':'patch','input':raw},call('read','read')])
    assert json.loads(messages[0]['tool_calls'][0]['function_call']['arguments'])=={'input':raw}
    assert messages[0]['tool_calls'][1]['function_call']['arguments']=='{"value":7}'
