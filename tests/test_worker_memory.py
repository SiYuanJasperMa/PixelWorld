from pathlib import Path
from pixelworld.scheduling import cgroup_memory, process_memory


def test_declared_worker_limit_uses_process_memory_when_cgroups_are_unavailable(tmp_path,monkeypatch):
    proc=tmp_path/'proc';(proc/'1').mkdir(parents=True);(proc/'2').mkdir()
    (proc/'1/smaps_rollup').write_text('Pss: 16384 kB\n')
    (proc/'2/status').write_text('VmRSS: 4096 kB\n')
    original=Path.read_text
    def read(path,*args,**kwargs):
        if str(path).startswith('/sys/fs/cgroup/'):raise FileNotFoundError(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    limit=32*1024**2
    assert process_memory(proc)==20*1024**2
    assert cgroup_memory(proc_root=proc,environment={'PIXELWORLD_WORKER_MEMORY_LIMIT_BYTES':str(limit)})==(20*1024**2,limit)


def test_missing_process_accounting_does_not_bypass_explicit_worker_limit(tmp_path,monkeypatch):
    original=Path.read_text
    def read(path,*args,**kwargs):
        if str(path).startswith('/sys/fs/cgroup/'):raise FileNotFoundError(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    limit=200*1024**3
    assert cgroup_memory(proc_root=tmp_path,environment={'PIXELWORLD_WORKER_MEMORY_LIMIT_BYTES':str(limit)})==(limit,limit)
