import json
import sys
from types import SimpleNamespace
import pytest

from tools import watch_dashboard_publication as publisher


def setup(tmp_path,monkeypatch):
    source=tmp_path/'bundle/publication/pixelworld';source.mkdir(parents=True)
    group={'suite':'suite','model_key':'model','track':'vision-agent','path':'suite/model/index.json'}
    (source/'catalogue.json').write_text(json.dumps({'groups':[group],'updated_utc':'first'}))
    (source/'experiment_plan.json').write_text(json.dumps({'models':[],'benchmarks':[]}))
    index=source/group['path'];index.parent.mkdir(parents=True)
    index.write_text(json.dumps({'cases':{},'counts':{'complete':1},'updated_utc':'first','generation_updated_utc':'first'}))
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(
        HfApi=lambda **kwargs:None,CommitOperationAdd=lambda **kwargs:SimpleNamespace(**kwargs)))
    batches=[]
    def commit(api,**kwargs):
        batches.append(kwargs['operations']);return SimpleNamespace(oid=str(len(batches)))
    monkeypatch.setattr(publisher,'dataset_commit',commit)
    return source,index,batches


def test_poll_timestamp_only_does_not_reupload_completed_group(tmp_path,monkeypatch):
    source,index,batches=setup(tmp_path,monkeypatch)
    publisher.hf_progress(source,None)
    x=json.loads(index.read_text());x.update(updated_utc='second',generation_updated_utc='second');index.write_text(json.dumps(x))
    x=json.loads((source/'catalogue.json').read_text());x['updated_utc']='second';(source/'catalogue.json').write_text(json.dumps(x))
    result=publisher.hf_progress(source,None)
    assert len(batches)==1
    assert result['changed_items']==0
    assert result['updated_utc']=='first'  # Last actually published snapshot.


def test_real_progress_change_is_published(tmp_path,monkeypatch):
    source,index,batches=setup(tmp_path,monkeypatch)
    publisher.hf_progress(source,None)
    x=json.loads(index.read_text());x['counts']['complete']=2;index.write_text(json.dumps(x))
    publisher.hf_progress(source,None)
    assert [op.path_in_repo for op in batches[-1]]==['live/suite/model/index.json']


def test_failed_commit_is_not_acknowledged(tmp_path,monkeypatch):
    source,index,batches=setup(tmp_path,monkeypatch)
    def fail(*args,**kwargs):raise RuntimeError('network failure')
    monkeypatch.setattr(publisher,'dataset_commit',fail)
    with pytest.raises(RuntimeError):publisher.hf_progress(source,None)
    assert not (source.parent.parent/'state/hf_progress_ack.json').exists()


def test_media_readiness_and_usage_change_digest():
    a={'updated_utc':'first','cases':{'case':[{'hf_detail_ready':False,'output_tokens':10}]}}
    b={'updated_utc':'second','cases':{'case':[{'hf_detail_ready':True,'output_tokens':10}]}}
    c={'updated_utc':'second','cases':{'case':[{'hf_detail_ready':False,'output_tokens':11}]}}
    assert publisher.progress_digest(a)!=publisher.progress_digest(b)
    assert publisher.progress_digest(a)!=publisher.progress_digest(c)
