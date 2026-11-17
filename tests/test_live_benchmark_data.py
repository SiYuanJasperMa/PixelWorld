import json
import numpy as np
from tools.live_benchmark_data import case_data, update


def test_time_bucket_pointer_is_immutable_and_names_an_existing_revision(tmp_path):
    from tools.live_benchmark_data import save_head_pointer
    p=save_head_pointer(tmp_path, 'a'*40, now=1794577801)
    assert json.loads(p.read_text())['revision']=='a'*40
    assert save_head_pointer(tmp_path, 'b'*40, now=1794577820)==p
    assert json.loads(p.read_text())['revision']=='a'*40
    newer=save_head_pointer(tmp_path, 'b'*40, now=1794577831)
    assert newer != p and json.loads(newer.read_text())['revision']=='b'*40


def make_run(root, status='running', video=True):
    run = root / 'generations/physics_iq_512w24fps_gpt_native/m/text/case_0001_attempt_01'
    (run / 'workspace').mkdir(parents=True)
    (run / 'logs').mkdir()
    (run / 'input_snapshot').mkdir()
    (run / 'run_manifest.json').write_text(json.dumps({
        'status':status, 'case_id':'0001', 'model_key':'m', 'model':'M',
        'track':'text', 'attempt':1, 'started_utc':'2026-11-13T00:00:00Z'}))
    (run / 'media_contract.json').write_text(json.dumps({'width':3,'height':2,'frames':2,'fps':4}))
    (run / 'logs/token_budget.json').write_text('{"reported_output_tokens":123}')
    (run / 'input_snapshot/prompt.txt').write_text('Move the ball.')
    (run / 'logs/events.jsonl').write_text(json.dumps({'type':'item.completed','item':{
        'type':'command_execution','command':'python generate.py','aggregated_output':'saved'}})+'\n')
    if video:
        np.savez_compressed(run/'workspace/pixels.npz',pixels=np.zeros((2,3,2),dtype=np.uint32))
    return run


def test_running_candidate_can_play_before_final_submission(tmp_path):
    run=make_run(tmp_path)
    output=tmp_path/'public'
    d=case_data(run,output)
    assert d['status']=='running'
    assert d['output_tokens']==123
    assert len(d['candidates'])==1
    assert (output/d['candidates'][0]['pixels']).is_file()
    assert d['events'][0]['command']=='python generate.py'
    assert json.loads((run/'run_manifest.json').read_text())['status']=='running'


def test_unsubmitted_run_has_tools_and_tokens_without_false_failure(tmp_path):
    run=make_run(tmp_path,video=False)
    d=case_data(run,tmp_path/'public')
    assert d['candidates']==[] and d['status']=='running'
    assert d['output_tokens']==123 and d['events']


def test_live_metadata_tracks_new_output_and_redacts_private_paths(tmp_path):
    run=make_run(tmp_path)
    suite=run.parents[2]
    (suite/'status.json').write_text(json.dumps({'cases':198,'groups':{
        'm/text':{'counts':{'running':1,'pending':197},'running_cases':['0001']}}}))
    (run/'logs/events.jsonl').write_text(json.dumps({'type':'item.completed','item':{
        'type':'command_execution','command':'python /mnt/private/generate.py','aggregated_output':'ok'}})+'\n')
    output=tmp_path/'live';cache={}
    update(tmp_path,output,cache)
    detail=output/'physics_iq_512w24fps_gpt_native/m/text/case_0001_attempt_01.json'
    assert '/mnt/private' not in detail.read_text()
    (run/'logs/token_budget.json').write_text('{"reported_output_tokens":456}')
    update(tmp_path,output,cache)
    assert json.loads(detail.read_text())['output_tokens']==456


def test_token_update_does_not_reencode_saved_video(tmp_path, monkeypatch):
    from tools import live_benchmark_data as live
    run=make_run(tmp_path)
    destination=tmp_path/'public'
    first=case_data(run,destination)
    (run/'logs/token_budget.json').write_text('{"reported_output_tokens":789}')
    monkeypatch.setattr(live,'export_candidate',lambda *_: (_ for _ in ()).throw(AssertionError('No RGB encoding for token-only changes')))
    second=case_data(run,destination,previous=first)
    assert second['output_tokens']==789
    assert second['candidates']==first['candidates']


def test_deferred_video_export_is_retried_without_a_new_generation(tmp_path, monkeypatch):
    from tools import live_benchmark_data as live
    run=make_run(tmp_path);destination=tmp_path/'public'
    real=live.export_candidate
    monkeypatch.setattr(live,'export_candidate',lambda *_: (_ for _ in ()).throw(OSError('memory headroom')))
    first=case_data(run,destination)
    assert first['candidates']==[]
    monkeypatch.setattr(live,'export_candidate',real)
    second=case_data(run,destination,previous=first)
    assert second['candidates']


def test_published_asset_reuse_needs_no_local_media_copy(tmp_path):
    from concurrent.futures import Future
    from tools.live_benchmark_data import merge_render_jobs
    detail={'first_frame':'assets/existing.png','candidates':[],'events':[],
            'case_id':'0001','attempt':1,'delivery_status':'complete','status':'complete','output_tokens':1}
    future=Future();future.set_result([('case_0001_attempt_01','suite/m/text/case_0001_attempt_01.json',[],detail)])
    pending={('suite/m/text','0001'):future};cache={};destination=tmp_path/'public'
    merge_render_jobs(destination,cache,pending,tmp_path/'render',{'assets/existing.png'})
    assert (destination/'suite/m/text/case_0001_attempt_01.json').exists()
    assert not pending


def test_incremental_publish_tracks_only_changed_public_files(tmp_path,monkeypatch):
    from tools import live_benchmark_data as live
    root=tmp_path/'public';monkeypatch.setattr(live,'PUBLICATION_ROOT',root)
    monkeypatch.setattr(live,'PUBLICATION_DIRTY',set())
    assert live.save(root/'index.json',{'count':1})
    assert not live.save(root/'index.json',{'count':1})
    live.save(tmp_path/'private-cache.json',{'private':True})
    assert live.PUBLICATION_DIRTY=={'pixelworld/index.json'}


def test_visual_returns_are_published_from_frozen_logs_only(tmp_path):
    from PIL import Image
    run=make_run(tmp_path,video=False)
    (run/'logs/tool_images').mkdir()
    Image.new('RGB',(2,2),'red').save(run/'logs/tool_images/read.png')
    (run/'logs/events.jsonl').write_text(json.dumps({'type':'item.completed','item':{
        'type':'full_trace_image_return','returned_images':[
            {'path':'tool_images/read.png','available':True},
            {'path':'../../private.png','available':True}]}})+'\n')
    output=tmp_path/'public'
    data=case_data(run,output)
    assert len(data['events'][0]['images'])==1
    assert (output/data['events'][0]['images'][0]).is_file()


def test_slow_case_read_does_not_block_live_counts(tmp_path):
    from tools.live_benchmark_data import async_update
    run=make_run(tmp_path)
    (run.parents[2]/'status.json').write_text(json.dumps({'cases':198,'groups':{
        'm/text':{'counts':{'running':1,'pending':197},'running_cases':['0001']}}}))
    class Pending:
        def done(self): return False
    class Pool:
        def submit(self,*_): return Pending()
    pending={};output=tmp_path/'public'
    async_update(tmp_path,output,{},Pool(),pending,tmp_path/'render')
    data=json.loads((output/'catalogue.json').read_text())
    assert data['groups'][0]['counts']=={'running':1,'pending':197}
    assert pending


def test_historical_backfill_is_bounded_and_excludes_current_cases(tmp_path):
    import shutil
    from tools.live_benchmark_data import render_backfill_set
    first=make_run(tmp_path)
    second=first.with_name('case_0002_attempt_01');shutil.copytree(first,second)
    p=second/'run_manifest.json';m=json.loads(p.read_text());m['case_id']='0002';p.write_text(json.dumps(m))
    rows=render_backfill_set(first.parent,'suite/m/text',tmp_path/'render',{},excluded={'0001'},limit=1)
    assert len(rows)==1 and rows[0][3]['case_id']=='0002'
    saved={rows[0][0]:{'stamp':rows[0][2]}}
    assert render_backfill_set(first.parent,'suite/m/text',tmp_path/'render',saved,excluded={'0001'},limit=1)==[]


def test_historical_work_has_separate_slots_from_live_candidates(tmp_path):
    from tools.live_benchmark_data import async_update,render_backfill_set,render_case_set
    run=make_run(tmp_path)
    (run.parents[2]/'status.json').write_text(json.dumps({'cases':198,'groups':{
        'm/text':{'counts':{'running':1,'pending':197},'running_cases':['0001']}}}))
    class Pending:
        def done(self):return False
    class Pool:
        def __init__(self):self.calls=[]
        def submit(self,fn,*args):self.calls.append(fn);return Pending()
    live,history=Pool(),Pool();pending={};backfill={}
    async_update(tmp_path,tmp_path/'public',{},live,pending,tmp_path/'render',history,backfill,[0])
    assert live.calls==[render_case_set] and history.calls==[render_backfill_set]
    assert len(pending)==1 and len(backfill)==1


def test_reused_detail_is_distinct_from_new_attempt_and_means_select_history(tmp_path,monkeypatch):
    import hashlib
    import shutil
    from tools import live_benchmark_data as live
    run=make_run(tmp_path,status='failed',video=False)
    old=tmp_path/'historical'/run.name
    shutil.copytree(run,old)
    p=old/'run_manifest.json';data=json.loads(p.read_text())
    data.update(status='complete',validation={'pass':True},token_usage={'output_tokens':321})
    p.write_text(json.dumps(data))
    root=run.parents[2]
    row={'model_key':'m','track':'text','case_id':'0001','source':str(old),
         'manifest_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
         'original_attempt':1,'source_suite':'previous','original_filesystem_policy':'workspace-write'}
    (root/'reuse_registry.json').write_text(json.dumps({'schema':'pixelworld-compatible-history-v1','entries':[row]}))
    monkeypatch.setattr(live,'export_candidate',lambda *_:None)
    rows=live.render_case_set(run.parent,'0001','suite/m/text',tmp_path/'render',{})
    assert len(rows)==2 and len({r[1]for r in rows})==2
    detail=next(r[3]for r in rows if r[3].get('reused'))
    assert detail['provenance']['original_filesystem_policy']=='workspace-write'
    assert str(tmp_path) not in json.dumps(detail)
    assert live.delivered_usage_means(run.parent)['mean_output_tokens']==321


def test_external_cache_import_adds_history_without_overwriting_live_state():
    from tools.live_benchmark_data import merge_external_cache
    current={'suite/m/vision-agent':{'case_live':{'status':'running','stamp':[2]}}}
    external={'suite/m/vision-agent':{
        'case_live':{'status':'stale','stamp':[1]},
        'history_old':{'status':'complete','stamp':[3]}}}
    merge_external_cache(current,external)
    assert current['suite/m/vision-agent']['case_live']['status']=='running'
    assert current['suite/m/vision-agent']['history_old']['status']=='complete'
