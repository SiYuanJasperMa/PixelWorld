import json
from types import SimpleNamespace
from tools.publish_hf_incremental import package_case, publish_cycle


def fixture(tmp_path):
    source=tmp_path/'public';group=source/'suite/model/vision-agent';group.mkdir(parents=True)
    relative='suite/model/vision-agent/case_0001_attempt_01.json'
    data={'case_id':'0001','status':'complete','delivery_status':'complete',
          'candidates':[{'preview_mp4':'assets/a/preview.mp4','poster':'assets/a/poster.png','pixels':'private.npz'}],
          'events':[], 'first_frame':'input.png','source_video':None}
    (source/relative).write_text(json.dumps(data))
    (group/'index.json').write_text(json.dumps({'cases':{'0001':[{'detail':relative,'attempt':1,'status':'complete'}]}}))
    (source/'catalogue.json').write_text(json.dumps({'groups':[{'path':'suite/model/vision-agent/index.json','latest_completion':['now','0001']}]}))
    return source,relative


def test_ready_commit_contains_media_and_detail_and_excludes_raw_inputs(tmp_path):
    source,relative=fixture(tmp_path)
    assets=source/'assets/a';assets.mkdir(parents=True)
    (assets/'preview.mp4').write_bytes(b'video');(assets/'poster.png').write_bytes(b'poster')
    calls=[]
    def commit(*_args,**kwargs):
        calls.append(kwargs['operations']);return SimpleNamespace(oid='abc')
    state=tmp_path/'state.json'
    publish_cycle(source,state,SimpleNamespace(create_commit=commit))
    paths={o.path_in_repo for o in calls[0]}
    assert paths=={'data/assets/a/preview.mp4','data/assets/a/poster.png','live/'+relative}
    detail=next(o.path_or_fileobj for o in calls[0] if o.path_in_repo.startswith('live/'))
    payload=json.loads(detail)
    assert payload['first_frame'] is None and payload['candidates'][0]['pixels'] is None
    assert payload['candidates'][0]['preview_mp4'].startswith('https://huggingface.co/')
    assert relative in json.loads(state.read_text())['ready']
    publish_cycle(source,state,SimpleNamespace(create_commit=commit))
    assert len(calls)==1


def test_missing_preview_is_not_published_as_ready(tmp_path):
    source,relative=fixture(tmp_path)
    assert package_case(source,relative) is None
    called=[]
    publish_cycle(source,tmp_path/'state.json',SimpleNamespace(create_commit=lambda *a,**k:called.append(1)))
    assert not called
