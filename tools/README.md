# Tools guide

Launch coding agents through the installed `pixelworld` CLI. Utilities here
prepare inputs, evaluate outputs, publish sanitized records and operate the
experiment services. They do not change the generation boundary.

## Operator entry

Read [the experiment runbook](../docs/EXPERIMENT_RUNBOOK.md) first. Current
operator code lives in `tools/ops/`; exact private runtime locations are supplied
through `runtime.local.json`, outside Git.

```bash
python -m tools.ops.status --map runtime.local.json
# Run on the CPU worker to verify CPU-owned processes:
python -m tools.ops.status --map runtime.local.json --role cpu --verify-processes
```

This command does not dispatch, score, upload or change a run. It distinguishes
saved counters from live processes and refuses duplicate or mismatched cohorts.
Existing `tools.MODULE` paths remain stable for archived configs and receipts.

## Current entry points

| Task | Module |
|---|---|
| Model and benchmark launch | `pixelworld doctor`, `run-case`, `matrix run/status` |
| Manage a restartable matrix | `tools.manage_matrix` |
| Authorize recorded failed-tail retries | `tools.watch_failed_retries` |
| Stage official RBench Common Manipulation inputs | `tools.stage_rbench_common` |
| RBench official GPT-6.1-Sol scoring | `tools.evaluate_rbench_common_gpt61` · `tools.watch_rbench_common_scores` |
| PhyGenBench three-stage MCQ scoring | `tools.evaluate_phygen_three_stage_gpt55` with the current GPT-6.1-Sol option |
| PCVE native evaluation | `tools.evaluate_pcve_full` · `tools.watch_pcve_gpu_evaluation` |
| Photographic appearance | `tools.evaluate_video_photographic_gpt61` |
| Physics-IQ reference-pool FVD | `tools.evaluate_physics_iq_fvd` |
| Export public case records | `tools.live_benchmark_data` |
| Upload videos and sanitized complete traces | `tools.publish_hf_incremental` |
| Update HF publication coverage | `tools.watch_hf_dataset_progress` |
| Publish and verify the main dashboard | `tools.publish_main_suite_hf` |
| Publish and verify HF repository cards | `tools.publish_hf_readmes` |
| Archive original I/O and attempts privately | `tools.archive_recent_registered_runs` |
| Recompute current accepted-run usage and explicit-tariff costs | `tools.build_registered_cost_snapshot` |
| Draw current main and analysis cost/score points | `tools.build_cost_score_plots` |

Use `python -m tools.MODULE --help` for arguments. Follow the supplied frozen
configs and [main protocol](../docs/MAIN_SUITE_20261124.md). A tool whose filename
contains an older judge/version can still implement the current versioned
adapter; the actual selected judge and output provenance determine the protocol.

## Historical utilities

Flash, Pi, RoboJudge and superseded initialization experiments remain available
for reproducibility. `tools.final_experiments`, `tools.publish_dashboard_snapshot`
and `tools.sync_stable_pixelworld_pages` are legacy launch/publication routes.
Use [HF publishing](../docs/HF_PUBLISHING.md) for current delivery. The canonical
source is [SiYuanJasperMa/PixelWorld](https://github.com/SiYuanJasperMa/PixelWorld).

Keep module paths stable for existing run receipts and service configs.
Historical tools are documented separately rather than moved out from under
running jobs. All generated data, outputs, scores and private credentials stay
outside the source repository.
