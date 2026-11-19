from concurrent.futures import Future
from tools.live_benchmark_data import cached_upstream_wait


class DeferredPool:
    def __init__(self):self.jobs=[]
    def submit(self,fn,*args):
        future=Future();self.jobs.append((fn,args,future));return future


def test_blocked_live_log_does_not_block_progress_writer(tmp_path):
    pool=DeferredPool();pending={};cache={}
    first=cached_upstream_wait('group',tmp_path,['case'],pool,pending,cache)
    second=cached_upstream_wait('group',tmp_path,['case'],pool,pending,cache)
    assert first['status']==second['status']=='unknown'
    assert first['waiting'] is None
    assert len(pool.jobs)==1


def test_completed_diagnostic_is_used_only_for_matching_running_cases(tmp_path):
    pool=DeferredPool();pending={};cache={}
    cached_upstream_wait('group',tmp_path,['first'],pool,pending,cache)
    pool.jobs[0][2].set_result({'waiting':1,'oldest_seconds':10})
    result=cached_upstream_wait('group',tmp_path,['first'],pool,pending,cache)
    assert result['waiting']==1 and result['status']=='known'
    changed=cached_upstream_wait('group',tmp_path,['second'],pool,pending,cache)
    assert changed['waiting'] is None and changed['status']=='unknown'


def test_finished_group_does_not_wait_for_obsolete_log_reader(tmp_path):
    pool=DeferredPool();pending={};cache={}
    cached_upstream_wait('group',tmp_path,['case'],pool,pending,cache)
    result=cached_upstream_wait('group',tmp_path,[],pool,pending,cache)
    assert result=={'waiting':0,'oldest_seconds':0,'status':'known'}
