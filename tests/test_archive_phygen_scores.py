import base64
import hashlib
import json
import tarfile

import pytest

from tools.archive_phygen_scores import GPT61_JUDGE, archive_pairs, exact_pair


def test_exact_judge_wire_readback_requires_image_and_reply_identity(tmp_path):
    proxy = tmp_path/'proxy'
    proxy.mkdir()
    image = b'original-frame-bytes'
    rid = 'resp_fixed'
    usage = {'input_tokens':1,'output_tokens':2,'total_tokens':3}
    prompt = 'Which action is visible?'
    decision = {'choice':'D'}
    call = {'prompt':prompt,'frames':[0],'frame_image_sha256':{'0':hashlib.sha256(image).hexdigest()},
            'response':decision,'usage':usage}
    request = {'model':GPT61_JUDGE,'input':[{'role':'user','content':[{'type':'input_text','text':prompt},
        {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(image).decode()}]}]}
    response = {'id':rid,'status':'completed','usage':usage,'output':[{'type':'message','content':[
        {'type':'output_text','text':json.dumps(decision)}]}]}
    for role,value in [('input',request),('response',response)]:
        (proxy/f'{rid}.{role}.json').write_text(json.dumps(value,indent=2))
    output = tmp_path/'wire'
    index = archive_pairs(proxy,output,{rid:call})
    with tarfile.open(output/index[rid]['shard']) as archive:
        assert archive.extractfile(f'{rid}/input.json').read() == (proxy/f'{rid}.input.json').read_bytes()
    with pytest.raises(ValueError,match='image bytes mismatch'):
        exact_pair(proxy,rid,{**call,'frame_image_sha256':{'0':'wrong'}})
    with pytest.raises(ValueError,match='decision differs'):
        exact_pair(proxy,rid,{**call,'response':{'choice':'A'}})
