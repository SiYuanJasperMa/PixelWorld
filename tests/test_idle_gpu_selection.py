from types import SimpleNamespace

from tools import run_gpu_evaluation


def test_gpu_with_free_memory_but_busy_compute_is_not_selected(monkeypatch):
    monkeypatch.setattr(run_gpu_evaluation.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(stdout='0, 75000, 87\n1, 60000, 0\n2, 70000, N/A\n'))
    assert run_gpu_evaluation.free_gpus() == [(1, 60000)]
