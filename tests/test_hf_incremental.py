import json
from types import SimpleNamespace
import pytest
from tools.publish_hf_incremental import package_case, publish_cycle


@pytest.fixture(autouse=True)
def no_live_publication_budget(monkeypatch):
    monkeypatch.setattr('tools.hf_commit_budget.commit',
                        lambda api, **kwargs: api.create_commit(**kwargs))


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


def test_publication_alternates_accepted_delivery_and_failed_history(tmp_path):
    source,relative=fixture(tmp_path)
    assets=source/'assets/a';assets.mkdir(parents=True)
    (assets/'preview.mp4').write_bytes(b'video');(assets/'poster.png').write_bytes(b'poster')
    failed=relative.replace('attempt_01','attempt_02')
    (source/failed).write_text(json.dumps({'case_id':'0001','status':'failed','delivery_status':'failed',
        'candidates':[],'events':[],'first_frame':None}))
    group=source/'suite/model/vision-agent/index.json'
    group.write_text(json.dumps({'cases':{'0001':[{'detail':relative,'attempt':1,'status':'complete'},
        {'detail':failed,'attempt':2,'status':'failed'}]}}))
    calls=[]
    def commit(*args,**kwargs):calls.append(kwargs['operations']);return SimpleNamespace(oid=str(len(calls)))
    api=SimpleNamespace(create_commit=commit);state=tmp_path/'state.json'
    publish_cycle(source,state,api,batch_size=1)
    first={x.path_in_repo for x in calls[0]}
    assert 'live/'+relative in first and 'live/'+failed not in first
    publish_cycle(source,state,api,batch_size=1)
    assert 'live/'+failed in {x.path_in_repo for x in calls[1]}


def test_old_trace_format_is_refreshed_without_changing_source_detail(tmp_path,monkeypatch):
    import hashlib
    source,relative=fixture(tmp_path/'bundle/source_parent')
    (source.parent.parent/'live_sources.json').write_text('{"sources":[]}')
    raw=(source/relative).read_bytes();digest=hashlib.sha256(raw).hexdigest()
    state=tmp_path/'state.json';state.write_text(json.dumps({'ready':{relative:{
        'source_sha256':digest,'trace_ready':True,'trace_format_version':2}}}))
    monkeypatch.setattr('tools.publish_hf_incremental.package_case',lambda *args:{
        'source_sha256':digest,'media':{},'detail':raw,'trace_ready':True,'trace_format_version':3})
    calls=[]
    api=SimpleNamespace(create_commit=lambda **kwargs:(calls.append(kwargs)or SimpleNamespace(oid='new')))
    publish_cycle(source,state,api)
    assert len(calls)==1 and (source/relative).read_bytes()==raw
    assert json.loads(state.read_text())['ready'][relative]['trace_format_version']==3
    publish_cycle(source,state,api)
    assert len(calls)==1
