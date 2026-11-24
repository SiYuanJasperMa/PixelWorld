import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import publish_main_suite_hf as pub


def status():
    return {'protocol':pub.PROTOCOL,'updated_utc':'2026-11-24T00:00:00+00:00','models':{},'references':{},
            'photographic_progress':{},'photographic_scores':{},'harness_progress':{}}


def test_dataset_readback_precedes_space_commit(tmp_path,monkeypatch):
    calls=[];cache={}
    class API:
        token=None
        def dataset_info(self,*a,**k):return SimpleNamespace(private=False)
        def space_info(self,*a,**k):return SimpleNamespace(private=False,sha='space-old')
        def create_commit(self,repo_id,**kwargs):
            calls.append(('commit',repo_id))
            for op in kwargs['operations']:cache[op.path_in_repo]=op.path_or_fileobj
            return SimpleNamespace(oid='dataset-new' if repo_id==pub.DATASET else 'space-new')
    def download(repo,name,**kwargs):
        calls.append(('readback',name))
        assert kwargs['revision']=='dataset-new'
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(cache[name]);return str(p)
    monkeypatch.setattr('tools.hf_commit_budget.commit',lambda api,**kw:api.create_commit(**kw))
    monkeypatch.setattr('huggingface_hub.hf_hub_download',download)
    monkeypatch.setattr(pub,'read_public',lambda *a:f'<meta content="{pub.PROTOCOL}">')
    proof=pub.publish(API(),status(),tmp_path/'proof.json')
    assert calls[0]==('commit',pub.DATASET)
    assert calls[-1]==('commit',pub.SPACE)
    assert sum(c[0]=='readback' for c in calls)==4
    assert ('readback','metadata/all_video_evaluation_progress.json') in calls
    assert proof['dataset_commit']=='dataset-new'
    assert json.loads((tmp_path/'proof_dataset.json').read_text())['commit']=='dataset-new'


def test_failed_dataset_readback_does_not_publish_space(tmp_path,monkeypatch):
    calls=[]
    api=SimpleNamespace(token=None,dataset_info=lambda *a,**k:SimpleNamespace(private=False))
    monkeypatch.setattr('tools.hf_commit_budget.commit',lambda *a,**k:SimpleNamespace(oid='bad'))
    p=tmp_path/'corrupt';p.write_bytes(b'wrong')
    monkeypatch.setattr('huggingface_hub.hf_hub_download',lambda *a,**k:str(p))
    monkeypatch.setattr(pub,'read_public',lambda *a:calls.append('space'))
    with pytest.raises(ValueError,match='readback'):
        pub.publish(api,status(),tmp_path/'proof.json')
    assert not calls
    assert not (tmp_path/'proof_dataset.json').exists()


def test_dataset_html_polls_the_dataset_and_keeps_space_links():
    files,_=pub.dataset_payload(status())
    html=files['dashboard/index.html']
    assert "metadata/main_suite_status.json" in html
    assert "dashboard/index.html" in html
    assert 'replaceWith(next)' in html
    assert 'location.replace' not in html
    assert 'tree/collaboration/' not in html


def test_shared_benchmark_links_use_current_browser_and_keep_model_query():
    files, _ = pub.payload(status())
    for filename, benchmark in pub.DETAIL_ALIASES.items():
        html = files[filename]
        assert 'data-benchmark="'+benchmark+'"' in html
        assert 'results_browser.js' in html
        assert 'results_browser.js?v=' in html
        assert 'formal.js' not in html
        assert 'location.replace' not in html


def test_detail_page_changes_refresh_the_publish_digest(tmp_path, monkeypatch):
    _, before = pub.payload(status())
    original = Path.read_text
    def changed(path, *args, **kwargs):
        body = original(path, *args, **kwargs)
        return body+'\n/* browser update */' if path.name == 'results_browser.js' else body
    monkeypatch.setattr(Path, 'read_text', changed)
    _, after = pub.payload(status())
    assert before != after


def test_all_video_progress_is_in_the_same_dataset_snapshot_and_changes_digest():
    first = status()
    first['all_video_evaluation_progress'] = {'schema_version':1,'updated_utc':'2026-11-25T02:00:00Z',
        'collection_complete':False,'available_video_records':14000,'services':[], 'groups':[],
        'pid':123,'private_path':'/private/producer'}
    files, before = pub.dataset_payload(first)
    progress = json.loads(files['metadata/all_video_evaluation_progress.json'])
    main = json.loads(files['metadata/main_suite_status.json'])
    assert progress == main['all_video_evaluation_progress']
    assert progress['available_video_records'] == 14000 and progress['collection_complete'] is False
    assert 'pid' not in progress and 'private_path' not in progress
    first['all_video_evaluation_progress']['available_video_records'] = 14001
    _, after = pub.dataset_payload(first)
    assert before != after
