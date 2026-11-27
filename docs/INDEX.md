# Documentation

Current code: https://github.com/SiYuanJasperMa/PixelWorld

Results: https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html

## Run main experiments

| Task | Guide |
|---|---|
| Understand the four benchmarks and protocol pins | [Current main suite](MAIN_SUITE_20261124.md) |
| Run an exact model with Codex 0.169.0 | [Add-model runbook](ADD_MODEL_RUNBOOK.md) |
| Claude collaborator: 537 Vision samples | [Claude assignment](RUN_CLAUDE_MAIN.md) |
| Claude Code 2.1.304 on a Claude subscription (sandboxed native tools) | [Subscription runbook](RUN_CLAUDE_CODE_SUBSCRIPTION.md) |
| Antigravity CLI 1.5.18 (gemini-4-neon-high) on a Google AI subscription | [Antigravity runbook](RUN_ANTIGRAVITY_SUBSCRIPTION.md) |
| Kimi/Qwen collaborator: 537 samples each | [Kimi + Qwen assignment](RUN_KIMI_QWEN_MAIN.md) |
| Hand a task to another collaborator | [Handoff template](COLLABORATOR_HANDOFF.md) |
| Choose configuration templates | [Configuration guide](../configs/README.md) |

## Evaluate and interpret results

| Task | Guide |
|---|---|
| Dispatch evaluation and monitor its coverage | [Automatic evaluation](AUTOMATIC_EVALUATION.md) |
| Keep accepted runs, evaluator versions and partial scores distinct | [Result provenance](RESULT_PROVENANCE.md) |
| Compare native video-model outputs | [Local baseline evaluation](PHYSICS_IQ_BASELINE_REEVALUATION.md) |
| Compare Codex, OpenCode and Claude Code | [Harness comparison](HARNESS_COMPARISON.md) |
| Review framework and analysis design | [Paper framework](FRAMEWORK_FOR_PAPER.md) |

Physical scores, GPT-6.1-Sol appearance scores and reference-pool FVD are
separate measurements. Full scores require complete, matching case coverage.

## Publish and operate

| Task | Guide |
|---|---|
| Use the canonical GitHub, HF and local locations | [Repository and publication ownership](REPOSITORY_AND_PUBLICATION.md) |
| Upload videos, sanitized complete traces and live progress | [HF publishing](HF_PUBLISHING.md) |
| Update the dataset and Space cards | [Dataset README](HF_DATASET_README.md) · [Space README](HF_SPACE_README.md) |
| Store and verify private originals | [Storage layout](STORAGE_LAYOUT.md) · [Storage policy](STORAGE_POLICY.md) |
| Record inputs, outputs, tokens and candidate history | [Recording contract](DATA_RECORDING_CONTRACT.md) |
| Retry failed deliveries without rerolling successful videos | [Retry policy](RETRY_POLICY.md) |
| Verify provider errors and preserve failed attempts during recovery | [Provider failure recovery](PROVIDER_FAILURE_RECOVERY.md) |
| Check filesystem and task boundaries | [Isolation](FILESYSTEM_ISOLATION.md) · [Workspace contract](WORKSPACE_AND_TASK.md) |
| Recover a stopped service | [Service recovery](SERVICE_RECOVERY.md) · [Monitoring recovery](MONITORING_RECOVERY.md) |
| Locate current command-line utilities | [Tools guide](../tools/README.md) |

## Historical material

[Archive index](archive/README.md) contains superseded protocols and experiments.
The [November 17 freeze](FINAL_EXPERIMENT_PROTOCOL.md),
[old main protocol](MAIN_EXPERIMENT_PROTOCOL.md), RoboJudge, Pi/DSH and Flash
records remain for provenance. Historical deployment URLs are not current
publishing targets. Use the current main suite and add-model runbook for new runs.
