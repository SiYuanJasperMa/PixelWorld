from tools.watch_minimax_pcve_backfill import select_tail_batch


def test_tail_batch_excludes_completed_and_inflight_cases():
    records={0:{'status':'completed'},1:{'status':'generating'},39:{'status':'completed'}}
    selected=select_tail_batch(records,list(range(40)),limit=12)
    assert len(selected)==12
    assert selected==sorted(selected,reverse=True)
    assert not(set(selected)&{0,1,39})


def test_backfill_requires_time_for_main_worker_not_to_reach_tail():
    assert select_tail_batch({},[1,2,3])==[]
    selected=select_tail_batch({},list(range(20)),limit=12)
    assert len(selected)<12
    assert min(selected)>0
