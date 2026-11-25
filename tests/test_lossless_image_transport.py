import base64
import io
import json

import numpy as np
from PIL import Image

from pixelworld.traex_image_transport import LIMIT, lossless_payload, split_image


def url(pixels):
    data=io.BytesIO();Image.fromarray(pixels).save(data,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()


def test_small_input_is_not_transformed():
    assert split_image(url(np.zeros((10,12,3),dtype=np.uint8))) is None


def test_tiling_preserves_every_pixel_and_existing_native_metadata():
    original=np.random.default_rng(7).integers(0,256,(900,1400,3),dtype=np.uint8)
    image=url(original)
    native={'extra_info':'{"tool_wire_contract":"preserve_v1"}',
            'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':image}}]}]}
    result=lossless_payload(native)
    metadata=json.loads(result['extra_info'])
    assert metadata['tool_wire_contract']=='preserve_v1'
    evidence=metadata['input_image_transport']['images'][0]
    restored=np.zeros_like(original)
    images=[p['image_url']['url'] for p in result['messages'][0]['content'] if p['type']=='image_url']
    assert len(images)==len(evidence['tiles']) and len(images)>1
    for encoded,tile in zip(images,evidence['tiles']):
        data=base64.b64decode(encoded.split(',',1)[1])
        assert len(data)<=LIMIT and len(encoded.split(',',1)[1])<5*1024*1024
        left,top,right,bottom=tile['bounds']
        restored[top:bottom,left:right]=np.asarray(Image.open(io.BytesIO(data)))
    assert np.array_equal(restored,original)
