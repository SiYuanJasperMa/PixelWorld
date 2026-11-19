from types import SimpleNamespace
import hashlib

from tools import run_gpu_evaluation


def test_gpu_with_free_memory_but_busy_compute_is_not_selected(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(stdout='0, 75000, 87\n1, 60000, 0\n2, 70000, N/A\n'))
    monkeypatch.setattr(run_gpu_evaluation,'gpu_app_snapshot',lambda:{i:{'apps':0,'used_mib':0}for i in [0,1,2]})
    monkeypatch.setattr(run_gpu_evaluation,'known_keepalive_devices',lambda:set())
    assert run_gpu_evaluation.free_gpus() == [(1, 60000)]


def test_verified_yielding_keepalive_does_not_block_free_gpu(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='0, 75000, 87\n'))
    monkeypatch.setattr(run_gpu_evaluation,'gpu_app_snapshot',lambda:{0:{'apps':1,'used_mib':550}})
    monkeypatch.setattr(run_gpu_evaluation,'known_keepalive_devices',lambda:{0})
    assert run_gpu_evaluation.free_gpus()==[(0,75000)]


def test_real_workload_is_excluded_even_when_idle(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='0, 50000, 0\n'))
    monkeypatch.setattr(run_gpu_evaluation,'gpu_app_snapshot',lambda:{0:{'apps':1,'used_mib':32000}})
    monkeypatch.setattr(run_gpu_evaluation,'known_keepalive_devices',lambda:set())
    assert run_gpu_evaluation.free_gpus()==[]


def test_extra_compute_app_is_excluded_even_with_keeper(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='0, 75000, 10\n'))
    monkeypatch.setattr(run_gpu_evaluation,'gpu_app_snapshot',lambda:{0:{'apps':2,'used_mib':550}})
    monkeypatch.setattr(run_gpu_evaluation,'known_keepalive_devices',lambda:{0})
    assert run_gpu_evaluation.free_gpus()==[]


def test_keepalive_name_without_verified_source_is_not_trusted(tmp_path):
    proc=tmp_path/'123';proc.mkdir();script=tmp_path/'spring_keepalive.py';script.write_text('verified fixture')
    (proc/'cmdline').write_bytes(b'\0'.join([b'python',str(script).encode(),b'--worker',b'--gpu',b'0'])+b'\0')
    (proc/'stat').write_text('123 (python) S')
    trusted={hashlib.sha256(script.read_bytes()).hexdigest()}
    assert run_gpu_evaluation.known_keepalive_devices(tmp_path,trusted)=={0}
    script.write_text('modified untrusted fixture')
    assert run_gpu_evaluation.known_keepalive_devices(tmp_path,trusted)==set()


def test_unknown_nvml_ownership_waits_instead_of_sharing_gpu(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='0, 75000, 0\n'))
    monkeypatch.setattr(run_gpu_evaluation,'gpu_app_snapshot',lambda:None)
    assert run_gpu_evaluation.free_gpus()==[]
