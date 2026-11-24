import base64
import hashlib
import json

import pytest

from tools.archive_photographic_scores import GPT61_JUDGE, exact_photo_pair


def test_photo_wire_preserves_exact_order_decision_and_usage(tmp_path):
    proxy = tmp_path/'proxy'
    proxy.mkdir()
    rid = 'resp_fixture'
    frames = [b'first',b'second']
    hashes = [hashlib.sha256(f).hexdigest() for f in frames]
    request = {'model':GPT61_JUDGE,'input':[{'role':'user','content':[
        {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(v).decode()}
        for v in frames]}]}
    usage = {'input_tokens':50,'output_tokens':2,'total_tokens':52}
    score = {'scores':[{'id':'A','score':3}],'pairs':[]}
    response = {'id':rid,'status':'completed','usage':usage,'output':[{'type':'message','content':[{
        'type':'output_text','text':json.dumps(score)}]}]}
    sdk = tmp_path/'sdk.json'
    sdk.write_text(json.dumps({'request':request,'response':response}))
    for role,obj in [('input',request),('response',response)]:
        (proxy/f'{rid}.{role}.json').write_text(json.dumps(obj))
    row = {**score,'response_id':rid,'anonymous_mapping':{'A':'model'},'usage':usage,
           'input_frame_hashes':{'A':[{'sha256':h} for h in hashes]}}
    assert set(exact_photo_pair(proxy,rid,(row,sdk))) == {'input.json','response.json'}
    row['input_frame_hashes']['A'].reverse()
    with pytest.raises(ValueError,match='image sequence'):
        exact_photo_pair(proxy,rid,(row,sdk))
