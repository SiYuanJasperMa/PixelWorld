from tools.watch_physics_evaluation import isolated_mask_runtime
from tools.live_evaluation_data import physics_score, PHYSICS_IQ_COMMIT, PHYSICS_IQ_DATASET_REVISION


def test_same_model_at_two_settings_has_distinct_generated_mask_storage(tmp_path):
    reference = tmp_path / 'references/physics-IQ-benchmark-verified'
    (reference / 'split-videos').mkdir(parents=True)
    (reference / 'video-masks/real').mkdir(parents=True)
    (reference / 'video-masks/generated').mkdir()
    old = reference / 'video-masks/generated/shared-old-mask'
    old.write_text('wrong-cohort')
    runtime = {'benchmark_parent': reference.parent}
    a = isolated_mask_runtime(runtime, tmp_path / 'eval/res480/m/vision')
    b = isolated_mask_runtime(runtime, tmp_path / 'eval/fps12/m/vision')
    relative = 'physics-IQ-benchmark-verified/video-masks/generated/model/24FPS'
    first = a['benchmark_parent'] / relative
    second = b['benchmark_parent'] / relative
    first.mkdir(parents=True)
    (first / 'mask').write_text('480p')
    second.mkdir(parents=True)
    assert not (second / 'mask').exists()
    assert first.resolve() != second.resolve()
    assert (a['benchmark_parent'] / 'physics-IQ-benchmark-verified/split-videos').resolve() == (reference / 'split-videos').resolve()
    assert old.read_text() == 'wrong-cohort'


def test_current_scores_require_isolated_mask_cache_attestation():
    row = {'inputs': 198, 'model_key': 'm', 'track': 'text', 'verified_score': 0.4,
           'original_formula_score': 0.4, 'upstream_commit': PHYSICS_IQ_COMMIT,
           'dataset_revision': PHYSICS_IQ_DATASET_REVISION}
    assert physics_score(row, 'm', 'text', require_isolated_cache=True) is None
    row['generated_mask_cache_revision'] = 'cohort-isolated-v1'
    assert physics_score(row, 'm', 'text', require_isolated_cache=True)['metrics']['Verified'] == 40
