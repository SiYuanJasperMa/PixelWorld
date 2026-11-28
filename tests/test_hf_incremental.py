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


def test_committed_trace_cache_is_removed_but_raw_trace_is_retained(tmp_path,monkeypatch):
    source,relative=fixture(tmp_path/'bundle/publication')
    cache=source.parent.parent/'hf_incremental_traces/case.tar.gz';cache.parent.mkdir();cache.write_bytes(b'compressed')
    raw=source.parent.parent/'original_trace.jsonl';raw.write_text('original')
    monkeypatch.setattr('tools.publish_hf_incremental.package_case',lambda *args:{
        'source_sha256':'hash','media':{'live/case.trace.tar.gz':cache},
        'trace_ready':True,'trace_format_version':3,'detail':b'{}'})
    state=tmp_path/'state.json'
    publish_cycle(source,state,SimpleNamespace(create_commit=lambda **kwargs:SimpleNamespace(oid='ok')))
    assert not cache.exists()
    assert raw.read_text()=='original'
    assert json.loads(state.read_text())['ready'][relative]['trace_ready']


def test_failed_commit_does_not_remove_trace_cache(tmp_path,monkeypatch):
    source,relative=fixture(tmp_path/'bundle/publication')
    cache=source.parent.parent/'hf_incremental_traces/case.tar.gz';cache.parent.mkdir();cache.write_bytes(b'compressed')
    monkeypatch.setattr('tools.publish_hf_incremental.package_case',lambda *args:{
        'source_sha256':'hash','media':{'live/case.trace.tar.gz':cache},
        'trace_ready':True,'trace_format_version':3,'detail':b'{}'})
    def fail(**kwargs):raise RuntimeError('upload failed')
    with pytest.raises(RuntimeError):publish_cycle(source,tmp_path/'state.json',SimpleNamespace(create_commit=fail))
    assert cache.read_bytes()==b'compressed'


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


def test_inline_generator_source_is_not_resolved_as_a_filename(tmp_path):
    source,relative=fixture(tmp_path)
    assets=source/'assets/a';assets.mkdir(parents=True)
    (assets/'preview.mp4').write_bytes(b'video');(assets/'poster.png').write_bytes(b'poster')
    data=json.loads((source/relative).read_text())
    inline='import numpy as np\n'+('# pixel generation\n'*900)
    data['candidates'][0]['code']=inline
    (source/relative).write_text(json.dumps(data))
    package=package_case(source,relative)
    public=json.loads(package['detail'])
    assert public['candidates'][0]['code_text']==inline
    assert public['candidates'][0]['code']==inline


def test_priority_group_batches_successes_without_skipping_failure_round(tmp_path):
    source,relative=fixture(tmp_path)
    assets=source/'assets/a';assets.mkdir(parents=True)
    (assets/'preview.mp4').write_bytes(b'video');(assets/'poster.png').write_bytes(b'poster')
    original=json.loads((source/relative).read_text())
    histories={}
    for number in range(1,4):
        case=f'{number:04d}'
        name=relative.replace('0001',case)
        data={**original,'case_id':case}
        (source/name).write_text(json.dumps(data))
        histories[case]=[{'detail':name,'attempt':1,'status':'complete'}]
    failed=relative.replace('attempt_01','attempt_02')
    (source/failed).write_text(json.dumps({'case_id':'0001','status':'failed','delivery_status':'failed',
        'candidates':[],'events':[],'first_frame':None}))
    histories['0001'].append({'detail':failed,'attempt':2,'status':'failed'})
    (source/'suite/model/vision-agent/index.json').write_text(json.dumps({'cases':histories}))
    calls=[]
    def commit(**kwargs):
        calls.append(kwargs['operations']);return SimpleNamespace(oid=str(len(calls)))
    state=tmp_path/'state.json';api=SimpleNamespace(create_commit=commit)
    publish_cycle(source,state,api,3,priority_groups=['suite/model/vision-agent'],priority_cases_per_group=3)
    details={op.path_in_repo for op in calls[0] if op.path_in_repo.startswith('live/')}
    assert details=={'live/'+relative.replace('0001',f'{n:04d}')for n in range(1,4)}
    assert 'live/'+failed not in details
    publish_cycle(source,state,api,3,priority_groups=['suite/model/vision-agent'],priority_cases_per_group=3)
    assert 'live/'+failed in {op.path_in_repo for op in calls[1]}


def test_priority_group_ignores_missing_media_and_leaves_other_groups_available(tmp_path):
    source,relative=fixture(tmp_path)
    other='other/model/vision-agent/case_0002_attempt_01.json'
    path=source/other;path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'case_id':'0002','status':'failed','delivery_status':'failed',
        'candidates':[],'events':[],'first_frame':None}))
    (path.parent/'index.json').write_text(json.dumps({'cases':{'0002':[{'detail':other,'attempt':1,'status':'failed'}]}}))
    (source/'catalogue.json').write_text(json.dumps({'groups':[
        {'path':'other/model/vision-agent/index.json'}, {'path':'suite/model/vision-agent/index.json'}]}))
    calls=[]
    api=SimpleNamespace(create_commit=lambda **kwargs:(calls.append(kwargs['operations'])or SimpleNamespace(oid='ok')))
    state=tmp_path/'state.json'
    publish_cycle(source,state,api,4,priority_groups=['suite/model/vision-agent'])
    assert len(calls)==1
    assert {op.path_in_repo for op in calls[0]}=={'live/'+other}
    assert relative not in json.loads(state.read_text())['ready']


def test_quota_wait_retries_commit_receipt_write_without_reupload(tmp_path,monkeypatch):
    import errno
    from pathlib import Path
    source,relative=fixture(tmp_path)
    assets=source/'assets/a';assets.mkdir(parents=True)
    (assets/'preview.mp4').write_bytes(b'video');(assets/'poster.png').write_bytes(b'poster')
    state=tmp_path/'state.json'
    original_open=Path.open
    rejected=[]
    def open_with_quota(path,*args,**kwargs):
        if path.name.startswith('state.json.tmp.') and not rejected:
            rejected.append(path)
            raise OSError(errno.EDQUOT,'fixture quota exhausted')
        return original_open(path,*args,**kwargs)
    monkeypatch.setenv('PIXELWORLD_PERSIST_STORAGE_WAIT','1')
    monkeypatch.setattr(Path,'open',open_with_quota)
    monkeypatch.setattr('pixelworld.util.time.sleep',lambda seconds:None)
    calls=[]
    api=SimpleNamespace(create_commit=lambda **kwargs:(calls.append(kwargs)or SimpleNamespace(oid='durable')))
    publish_cycle(source,state,api)
    assert len(rejected)==1 and len(calls)==1
    assert json.loads(state.read_text())['ready'][relative]['commit']=='durable'
