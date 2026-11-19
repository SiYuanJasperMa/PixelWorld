import fcntl

from tools import run_gpu_group as group


def test_group_requires_enough_idle_memory(monkeypatch,tmp_path):
    monkeypatch.setattr(group,'free_gpus',lambda:[(0,80000),(1,30000),(2,80000)])
    assert group.claim_group(tmp_path,3,70000) is None


def test_group_excludes_existing_claims_and_keeps_coordination(monkeypatch,tmp_path):
    monkeypatch.setattr(group,'free_gpus',lambda:[(i,80000)for i in range(5)])
    occupied=(tmp_path/'gpu_1.lock').open('a');fcntl.flock(occupied,fcntl.LOCK_EX|fcntl.LOCK_NB)
    indices,handles=group.claim_group(tmp_path,4,70000)
    assert indices==(0,2,3,4)
    assert group.claim_group(tmp_path,1,70000) is None
    for handle in handles:handle.close()
    occupied.close()
