# Physics-IQ baseline reevaluation

`tools/evaluate_physics_baseline_subset.py` evaluates already generated local
baseline videos with the repository's pinned Physics-IQ Verified implementation.
It calls the pristine upstream `generate_mask`, `process_view`, and `IQTable`
per-view normalization methods. It records the official source commit, dataset
revision, source video hashes, reference video and mask hashes, input provenance,
native media dimensions, frame rate, and selected cases in external receipts.
Generated masks belong to a separate directory for each evaluation cohort.

The local Seedance 2.5 first-pass collection contains 197 of the 198 official
take-1 cases. Case `0055` has no generated video. Its preserved 198 source images
and base BPP prompts were checked against the pinned input manifest before
reevaluation. The self-refine collection is a separate experiment with additional
generation budget and is not the first-pass baseline.

The local MiniMax H3 collection contains first-pass and self-correct raw videos
for seven distinct official case identities. Three identities have preserved
normalized scoring videos and verified input/prompt provenance, so the current
reevaluation uses that three-view smoke. Raw video availability and evaluated
case coverage are reported separately.

The utility accepts only pinned take-1 filenames and decodable native 24 FPS,
120-frame videos. It preserves the submitted frames. Local Seedance normalized
videos retain frames 0–119, including the conditioning frame. The published
Seedance submission describes frames 1–120 from a 121-frame output. These are
different temporal conventions; the local reevaluation does not reproduce the
published submission or its independent-run statistics.

The upstream full-evaluation CLI requires all 198 generated videos. This
explicit subset utility therefore calls the original per-view functions without
modifying the upstream source or creating missing videos, masks, or perspective
metrics. Its partial result averages the official normalized scores of observed
views only. `official_full_score` remains false. Partial scores and smoke scores
are excluded from the complete-cohort main result table, and published reference
scores remain separately attributed to their source.

Use an existing pinned evaluator environment and reference resource directory;
keep video sources, provenance, outputs and temporary files in shared storage:

```bash
PYTHONPATH="$REPO_ROOT" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 MPLBACKEND=Agg \
  "$EVALUATOR_PYTHON" tools/evaluate_physics_baseline_subset.py \
  --video-dir "$BASELINE_VIDEO_DIR" \
  --provenance "$BASELINE_PROVENANCE" \
  --cohort seedance25-bpp-base-partial197 \
  --resource-root "$PHYSICS_RESOURCE_ROOT" \
  --evaluator-python "$EVALUATOR_PYTHON" \
  --output "$EVALUATION_ROOT/seedance25-bpp-base-partial197" \
  --workers 1 --wait-for-memory
```

Use `--case ID` for an initial case smoke. Completed per-case metrics can be
resumed from `checkpoint.json` when the source video hashes still match. The
evaluator applies the existing native-frame memory guard; absence of an exposed
container quota is recorded separately and does not justify adding concurrent
workers. The initial remote run uses one CPU evaluator process at a time and
records its observed memory use.

`input_inventory.json` records the selected input identities and missing cases.
`execution.json` records progress. `checkpoint.json` stores per-case official
metrics and normalized scores. Completed partial results use
`partial_verified_score_fraction` and `partial_verified_score_percent` in
`scores.json`, rather than a full-cohort score field. A separate progress summary
reports the source pool size, eligible scoring count, completed count and missing
identities. Small receipts and results can be mirrored to the publication archive
without copying original baseline videos or the reference pool.

The per-view normalization adapter was checked against the pinned `IQTable`
three-view aggregation on preserved smoke metrics, with agreement at floating
point precision. A fresh official mask and per-view metric smoke completed before
the larger local baseline evaluation was admitted.
