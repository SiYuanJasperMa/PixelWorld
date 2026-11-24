# PixelWorld documentation index

## Current main suite

- [MAIN_SUITE_20261124.md](MAIN_SUITE_20261124.md) — current Physics-IQ Verified
  I2V198, RBench Common Manipulation I2V50, PhyGenBench T2V160 and full
  PCVE-RigidBench V2V129; pinned evaluators and admission gates.
- [ADD_MODEL_RUNBOOK.md](ADD_MODEL_RUNBOOK.md) — Codex 0.169.0, exact model
  routing, supplied frozen configs, smoke reuse and per-benchmark matrix CLI.
- [COLLABORATOR_HANDOFF.md](COLLABORATOR_HANDOFF.md) — copy-paste assignment
  for one exact model version under the current suite.
- [RUN_CLAUDE_MAIN.md](RUN_CLAUDE_MAIN.md) — Claude collaborator: four full
  Vision main benchmarks, 537 official samples.
- [RUN_KIMI_QWEN_MAIN.md](RUN_KIMI_QWEN_MAIN.md) — Kimi/Qwen collaborator: four
  full Vision main benchmarks per model, 1,074 model-by-sample tasks in total.
- [HARNESS_COMPARISON.md](HARNESS_COMPARISON.md) — separate Codex 0.169.0 /
  OpenCode 1.19.6 / Claude Code 2.1.304 analysis and admission evidence; Pi legacy.

## Local baseline evaluation

- [PHYSICS_IQ_BASELINE_REEVALUATION.md](PHYSICS_IQ_BASELINE_REEVALUATION.md) — local VideoGen subset reevaluation and published-score distinctions.

## Publication and durable storage

- [HF_PUBLISHING.md](HF_PUBLISHING.md) — dataset/media/trace pipeline,
  dataset-first snapshot readback, Space publication and open-tab refresh.
- [HF_DATASET_README.md](HF_DATASET_README.md) and [HF_SPACE_README.md](HF_SPACE_README.md)
  — tracked sources for the public dataset and dashboard repository cards.
- [STORAGE_LAYOUT.md](STORAGE_LAYOUT.md) — private artifact ownership,
  LF3data archive and local working/publication directories.
- [STORAGE_POLICY.md](STORAGE_POLICY.md) — durable storage, archive readback
  and verified pruning rules.
- [DATA_RECORDING_CONTRACT.md](DATA_RECORDING_CONTRACT.md) — native originals,
  normalized events, usage coverage and candidate/code provenance.
- [RESULT_PROVENANCE.md](RESULT_PROVENANCE.md) — retained-result provenance
  and configuration audits; older results require compatibility checks.

Public entry points:
[HF dataset](https://huggingface.co/datasets/HuggingFriends/PixelWorld) ·
[Dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html).
Publication coverage compares current hashes with upload receipts; it is not
an exhaustive remote byte audit.

## Generation and operations

- [CODEX.md](CODEX.md) — Codex routing, clean context and track boundaries.
- [FILESYSTEM_ISOLATION.md](FILESYSTEM_ISOLATION.md) — `case-only-v1` isolation.
- [WORKSPACE_AND_TASK.md](WORKSPACE_AND_TASK.md) — workspace and task contract.
- [RETRY_POLICY.md](RETRY_POLICY.md) — general five-attempt cap and infra
  exemption; the current RBench protocol overrides this with one real candidate.
- [AUTOMATIC_EVALUATION.md](AUTOMATIC_EVALUATION.md) — scoring and GPU dispatch;
  current suite evaluator pins take precedence over historical cohorts.
- [MONITORING_RECOVERY.md](MONITORING_RECOVERY.md)
- [SERVICE_RECOVERY.md](SERVICE_RECOVERY.md)
- [PHYGENBENCH_PIXELSPACE_PAIRED_V2.md](PHYGENBENCH_PIXELSPACE_PAIRED_V2.md)
- [T2V_PHOTOGRAPHIC_TARGET.md](T2V_PHOTOGRAPHIC_TARGET.md)

## Paper and historical provenance

- [FRAMEWORK_FOR_PAPER.md](FRAMEWORK_FOR_PAPER.md) — framework and analysis design.
- [FINAL_EXPERIMENT_PROTOCOL.md](FINAL_EXPERIMENT_PROTOCOL.md) — November 17
  historical freeze, older model matrix, budgets and reuse provenance.
- [MAIN_EXPERIMENT_PROTOCOL.md](MAIN_EXPERIMENT_PROTOCOL.md) — historical public
  benchmark reference; consult the current suite for its RBench replacement.
- [PHYSICS_BENCHMARK_CANDIDATES.md](PHYSICS_BENCHMARK_CANDIDATES.md) — benchmark scouting.
- [archive/README.md](archive/README.md) — superseded experiments and protocols.

RoboJudge results remain historical. `tools/final_experiments.py` retains the
old suite mapping; use the current add-model runbook to launch four frozen
per-benchmark configs rather than treating that legacy launcher as current.
