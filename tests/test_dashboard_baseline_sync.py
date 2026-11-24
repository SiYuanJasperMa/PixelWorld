import json
import sys
from types import SimpleNamespace

from tools import watch_dashboard_publication
from tools.phygen_protocol import THREE_STAGE_JUDGE_FOLDER, THREE_STAGE_REVISION


def test_live_progress_merges_new_baseline_without_frontend_rebuild(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    source = bundle / "publication" / "pixelworld"
    source.mkdir(parents=True)
    (source / "catalogue.json").write_text(json.dumps({"groups": [], "updated_utc": "now"}))
    (source / "experiment_plan.json").write_text(json.dumps({"models": [], "benchmarks": []}))
    result = bundle / "evaluation" / THREE_STAGE_JUDGE_FOLDER
    result.mkdir(parents=True)
    (result / "summary.json").write_text(json.dumps({
        "version": THREE_STAGE_REVISION, "full160": True,
        "models": {"seedance25-v1": {"cases": 160, "mean": 0.7}}, "failures": []
    }))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        HfApi=lambda **kwargs: None,
        CommitOperationAdd=lambda **kwargs: SimpleNamespace(**kwargs),
    ))
    captured = []

    def commit(api, **kwargs):
        captured.extend(kwargs["operations"])
        return SimpleNamespace(oid="test-commit")

    monkeypatch.setattr(watch_dashboard_publication, "dataset_commit", commit)
    watch_dashboard_publication.hf_progress(source, None)
    operation = next(item for item in captured if item.path_in_repo == "live/experiment_plan.json")
    plan = json.loads(operation.path_or_fileobj)
    baseline = next(item for item in plan["video_baselines"] if item["key"] == "seedance25")
    assert baseline["scores"]["phygenbench"]["value"] == 0.7
    assert baseline["scores"]["phygenbench"]["kind"] == "matched_rerun"
    assert baseline["scores"]["phygenbench"]["judge"] == "GPT-5.5 three-stage"
    assert "video_baselines" not in json.loads((source / "experiment_plan.json").read_text())


def test_legacy_single_stage_baseline_is_not_published(tmp_path):
    from tools.baseline_data import video_baselines
    result = tmp_path / "evaluation" / "seedance25_phygen_v1_gpt55"
    result.mkdir(parents=True)
    (result / "summary.json").write_text(json.dumps({
        "models": {"seedance25-v1": {"cases": 160, "mean_0_1": 0.7}}, "failures": []
    }))
    baseline = next(row for row in video_baselines(tmp_path) if row["key"] == "seedance25")
    assert "phygenbench" not in baseline["scores"]
