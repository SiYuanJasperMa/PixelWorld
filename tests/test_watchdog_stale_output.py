import os
from tools import watch_benchmark_services as watcher


def service(tmp_path):
    pid=tmp_path/'pid';pid.write_text('123')
    marker=tmp_path/'output.json';marker.write_text('{}');os.utime(marker,(100,100))
    return {'name':'publisher','pid_file':str(pid),'health_path':str(marker),
            'identity':['publisher'],'max_output_age_seconds':900}


def test_non_opted_in_service_is_not_recovered(tmp_path,monkeypatch):
    svc=service(tmp_path);svc.pop('health_path')
    monkeypatch.setattr(watcher,'recover_disk_dispatcher',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not signal')))
    assert not watcher.recover_stale_output(svc,{}, {},now=100)


def test_stale_output_escalates_only_after_grace(tmp_path,monkeypatch):
    svc=service(tmp_path);calls=[]
    monkeypatch.setattr(watcher,'recover_disk_dispatcher',lambda *a,**k:calls.append(k['signal_number'])or True)
    recoveries={}
    assert watcher.recover_stale_output(svc,recoveries,{},now=100)
    assert watcher.recover_stale_output(svc,recoveries,{},now=120)
    assert watcher.recover_stale_output(svc,recoveries,{},now=161)
    import signal
    assert calls==[signal.SIGTERM,signal.SIGKILL]


def test_new_service_has_startup_grace(tmp_path,monkeypatch):
    svc=service(tmp_path)
    monkeypatch.setattr(watcher,'recover_disk_dispatcher',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not signal')))
    assert not watcher.recover_stale_output(svc,{}, {'publisher':{'pid':123,'time':100}},now=110)


def test_fresh_output_clears_recovery(tmp_path,monkeypatch):
    svc=service(tmp_path);os.utime(svc['health_path'],None);recoveries={'publisher':{'pid':123,'time':0}}
    monkeypatch.setattr(watcher,'recover_disk_dispatcher',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not signal')))
    assert not watcher.recover_stale_output(svc,recoveries,{},now=100)
    assert not recoveries


def test_failed_pid_receipt_does_not_hide_a_live_child(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    svc = service(tmp_path)
    svc['identity'] = [next(arg.decode() for arg in
                           Path('/proc/self/cmdline').read_bytes().split(b'\0') if arg)]
    starts = {}
    original = Path.write_text

    def fail_receipt(path, *args, **kwargs):
        if path.name.endswith('.watchdog.tmp'):
            raise OSError(errno.EDQUOT, 'Disk quota exceeded')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_receipt)
    assert 'Disk quota exceeded' in watcher.record_start(svc, os.getpid(), starts)
    assert Path(svc['pid_file']).read_text() == '123'
    assert watcher.service_pid(svc, starts['publisher']) == os.getpid()
