# Configuration guide

Use [the current main suite](../docs/MAIN_SUITE_20261124.md) and
[the add-model runbook](../docs/ADD_MODEL_RUNBOOK.md) for new experiments.
The owner supplies the frozen per-benchmark configs, input hashes and licensed
input manifests privately. The examples here describe interfaces; they do not
select the complete current benchmark suite.

## Current interfaces

| Template | Purpose |
|---|---|
| [collaborator-model.example.json](collaborator-model.example.json) | One exact model/provider, Vision main track |
| [final-models.example.json](final-models.example.json) | Model roster interface; check the current protocol before using it |
| [physics-iq-720p24fps.example.json](physics-iq-720p24fps.example.json) | Physics-IQ media adapter example |
| [pcve-full.example.json](pcve-full.example.json) | PCVE full-set adapter example |
| [hf-publication-protocol.json](hf-publication-protocol.json) | Public dataset and dashboard metadata |

Main generation uses Codex 0.169.0, case-only isolation, 720p/24 FPS and 1M
reported output tokens per real attempt. PCVE has 96 frames; the other three
benchmarks have 120. RBench's original candidate allowance is one; other main
benchmarks allow five real attempts. Infra failures are exempt. Explicit owner
requests to finish a sparse failed tail are separately recorded under
[the retry policy](../docs/RETRY_POLICY.md).

## Historical examples

`benchmark.example.json`, RoboJudge/OpenVE/VBench examples,
`benchmark_portfolio_20261109.json`, retired 4K and other smoke configs are
historical interfaces. They are retained for reproducibility and must not be
mistaken for a current full-matrix launch. Preserve their original recorded
budgets and provenance when reusing old deliveries.

Never place a credential in a config. `env_key` names a private parent-shell
variable. Keep active configs and run roots on the owner's durable shared
storage; do not commit private paths, inputs or generated artifacts.
