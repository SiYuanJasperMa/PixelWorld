import json

from pixelworld.util import sha256
from tools.generate_minimax_phygen import selected_cases
from tools.watch_minimax_phygen_backfill import can_import,import_completed,batch_needs_recovery


def test_private_selection_keeps_official_case_mapping_and_default_order():
    rows=[{'caption':f'caption{i}'}for i in range(1,161)]
    assert selected_cases(rows)==list(enumerate(rows,1))
    assert selected_cases(rows,[160,149])==[(160,rows[159]),(149,rows[148])]
    import pytest
    with pytest.raises(ValueError):selected_cases(rows,[1,1])


def test_delivery_requires_two_pending_cases_and_preserves_active_or_accepted():
    assert can_import(10,{},7)
    assert not can_import(10,{},8)
    assert not can_import(10,{10:{'status':'completed'}},1)
    assert not can_import(10,{10:{'status':'generating'}},1)
    assert not can_import(10,{8:{'status':'completed'},9:{'status':'completed'}},7)
    assert not can_import(10,{},None)


def test_dead_waiting_launcher_is_recovered_but_unknown_health_is_not():
    waiting={'launcher_pid':7,'phase':'waiting_for_free_gpu_group'}
    assert batch_needs_recovery(waiting,7,False)
    assert not batch_needs_recovery(waiting,7,True)
    assert not batch_needs_recovery(waiting,7,None)
    assert batch_needs_recovery({'launcher_pid':7,'phase':'failed'},7,True)


def test_valid_private_clip_is_committed_without_touching_existing_cases(tmp_path):
    private=tmp_path/'private';base=tmp_path/'base';case=10
    (private/'cases/phygen_010').mkdir(parents=True);(private/'videos').mkdir()
    raw=private/'cases/phygen_010/raw.mp4';video=private/'videos/phygen_010.mp4'
    raw.write_bytes(b'raw');video.write_bytes(b'video')
    record={'case':10,'task_type':'t2v','seed':42,'status':'completed',
        'model_revision':'42ed227ee7df40d41602854ae760620d6eb651fe',
        'evaluated_media':{'frames':120,'fps':24},'export_sha256':sha256(video),'raw_sha256':sha256(raw)}
    (private/'cases/phygen_010/generation.json').write_text(json.dumps(record))
    base.mkdir();(base/'progress.json').write_text(json.dumps({'case':1}))
    assert import_completed(private,base,case,{},1,1)
    assert (base/'videos/phygen_010.mp4').read_bytes()==b'video'
    committed=json.loads((base/'cases/phygen_010/generation.json').read_text())
    assert committed['producer']=='a800-private-tail-batch'
    assert not import_completed(private,base,case,{case:committed},1,1)


def test_primary_advancing_during_copy_prevents_commit(tmp_path,monkeypatch):
    from tools import watch_minimax_phygen_backfill as tool
    private=tmp_path/'private';base=tmp_path/'base'
    (private/'cases/phygen_010').mkdir(parents=True);(private/'videos').mkdir()
    raw=private/'cases/phygen_010/raw.mp4';video=private/'videos/phygen_010.mp4'
    raw.write_bytes(b'raw');video.write_bytes(b'video')
    record={'case':10,'task_type':'t2v','seed':42,'status':'completed',
        'model_revision':'42ed227ee7df40d41602854ae760620d6eb651fe',
        'evaluated_media':{'frames':120,'fps':24},'export_sha256':sha256(video),'raw_sha256':sha256(raw)}
    (private/'cases/phygen_010/generation.json').write_text(json.dumps(record))
    base.mkdir();(base/'progress.json').write_text(json.dumps({'case':1}))
    def advancing_copy(source,target):
        target.write_bytes(source.read_bytes())
        (base/'progress.json').write_text(json.dumps({'case':9}))
    monkeypatch.setattr(tool.shutil,'copyfile',advancing_copy)
    assert not import_completed(private,base,10,{},1,1)
    assert not(base/'videos/phygen_010.mp4').exists()
    assert not(base/'cases/phygen_010/generation.json').exists()
