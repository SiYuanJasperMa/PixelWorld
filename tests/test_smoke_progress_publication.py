from types import SimpleNamespace
import json
import sys

from tools.watch_phygen_acd_smoke import publish,REMOTE
from tools import hf_commit_budget


def setup(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir();token=tmp_path/'token';token.write_text('fixture-token')
    batches=[]
    def commit(api,**kwargs):batches.append(kwargs['operations']);return SimpleNamespace(oid=str(len(batches)))
    monkeypatch.setattr(hf_commit_budget,'commit',commit)
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(
        HfApi=lambda **kwargs:None,CommitOperationAdd=lambda **kwargs:SimpleNamespace(**kwargs)))
    return public,token,batches


def test_unchanged_smoke_poll_does_not_create_commit(tmp_path,monkeypatch):
    public,token,batches=setup(tmp_path,monkeypatch)
    publish(tmp_path,public,{'updated_unix':1,'cases':[]},token)
    publish(tmp_path,public,{'updated_unix':2,'cases':[]},token)
    assert len(batches)==1


def test_video_link_requires_atomic_media_upload(tmp_path,monkeypatch):
    public,token,batches=setup(tmp_path,monkeypatch)
    path=public/'D/case/video.mp4';path.parent.mkdir(parents=True);path.write_bytes(b'fixture-video')
    def summary(t):return {'updated_unix':t,'cases':[{'routes':{'D':{'state':'complete','video':REMOTE+'D/case/video.mp4'}}}]}
    publish(tmp_path,public,summary(1),token)
    first=json.loads(batches[0][-1].path_or_fileobj)
    assert 'video' not in first['cases'][0]['routes']['D']
    assert first['cases'][0]['routes']['D']['media_sync_pending']
    publish(tmp_path,public,summary(2),token)
    assert any(op.path_in_repo.endswith('video.mp4') for op in batches[1])
    second=json.loads(next(op.path_or_fileobj for op in batches[1] if op.path_in_repo.endswith('progress.json')))
    assert second['cases'][0]['routes']['D']['video']==REMOTE+'D/case/video.mp4'


def test_new_route_video_is_sent_before_reused_baseline(tmp_path,monkeypatch):
    public,token,batches=setup(tmp_path,monkeypatch)
    for route in ['A','C','D']:
        path=public/route/'case/video.mp4';path.parent.mkdir(parents=True);path.write_bytes(b'fixture-video')
    publish(tmp_path,public,{'updated_unix':1,'cases':[]},token)
    publish(tmp_path,public,{'updated_unix':2,'cases':[]},token)
    videos=[op.path_in_repo for op in batches[1] if op.path_in_repo.endswith('mp4')]
    assert videos[0].endswith('C/case/video.mp4') and videos[-1].endswith('A/case/video.mp4')


def test_large_trace_receives_leading_slot_every_third_media_batch(tmp_path,monkeypatch):
    public,token,batches=setup(tmp_path,monkeypatch)
    trace=public/'C/case/trace.tar.gz';trace.parent.mkdir(parents=True);trace.write_bytes(b'x'*(9*1024*1024))
    for i in range(20):(trace.parent/f'{i:02d}.png').write_bytes(b'fixture-image')
    publish(tmp_path,public,{'updated_unix':1,'cases':[]},token)
    for tick in [2,3,4]:publish(tmp_path,public,{'updated_unix':tick,'cases':[]},token)
    assert any(op.path_in_repo.endswith('trace.tar.gz')for op in batches[-1])
