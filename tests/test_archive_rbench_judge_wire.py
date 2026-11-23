import json
from types import SimpleNamespace

from tools.archive_rbench_judge_wire import key, resolve_call


def test_rbench_link_requires_a_unique_exact_request_and_decision(tmp_path):
    judge = SimpleNamespace(extract_json=json.loads)
    request = {'model':'judge','input':[{'type':'input_image','image_url':'data:image/jpeg;base64,exact'}]}
    expected = {'score':3}

    def make(name, score):
        path = tmp_path/f'{name}.json'
        path.write_text(json.dumps({'request':request,'response':{'id':name,'status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'score':score})}]}]}}))
        return path

    a,b,c=make('resp_a',3),make('resp_b',4),make('resp_c',3)
    assert resolve_call({key(request):[a,b]},request,expected,judge)['id']=='resp_a'
    assert resolve_call({key(request):[a,b,c]},request,expected,judge) is None
    assert resolve_call({key(request):[a,b]}, {**request,'model':'another'}, expected,judge) is None
