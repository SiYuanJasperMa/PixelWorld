# Add a main-suite model

Run one exact model version with the frozen Codex harness and supplied
per-benchmark configs. The current suite is specified in
[MAIN_SUITE_20261124.md](MAIN_SUITE_20261124.md); the portable launch command is
`pixelworld matrix run`. `tools/final_experiments.py` still maps the older
RoboJudge suite and does not launch the current four-benchmark protocol.

## 1. Pin the runtime and model route

Use **Codex CLI 0.169.0**, Python 3.11+, `git`, `ffmpeg`, `uv` and the case
sandbox. Record the exact repository commit and executable version. Set
`PIXELWORLD_CODEX` to the pinned executable, visible to the sandbox runtime:

```bash
codex --version
export PIXELWORLD_CODEX="$(command -v codex)"
```

Use a built-in Codex provider or a Responses-compatible gateway. Export the
API key privately in the parent environment. Store only its variable name in
an ignored local config. Use the provider's exact versioned model ID rather
than a family alias.

Example model entry inside a supplied benchmark config:

```json
{
  "key": "my-model",
  "model": "provider/exact-versioned-model-id",
  "reasoning_effort": "high",
  "cli_backend": "codex",
  "tracks": ["vision-agent"],
  "provider": {
    "type": "responses",
    "id": "my_gateway",
    "name": "My Responses gateway",
    "base_url": "https://api.example.com/v1",
    "env_key": "MY_MODEL_API_KEY"
  }
}
```

[collaborator-model.example.json](../configs/collaborator-model.example.json)
is a model/route roster template, not a runnable current-suite benchmark
config. The eight-model roster already includes GPT-6.2-Sol; adding another
version requires a distinct model key and provenance.

## 2. Obtain the four frozen configs

The owner supplies the benchmark configs, permitted inputs, exact case IDs,
input hashes and evaluator pins through private storage. Licensed benchmark
inputs are not distributed through the code repository. Copy configs to
ignored `*.local.json` files, preserving their protocol, inputs and case list;
set the new model/provider and a separate run root for that model/batch.

| Benchmark | Required scope | Output | Real-attempt allowance |
|---|---|---|---|
| Physics-IQ Verified | 198 I2V cases | 1280×720, 24 FPS, 120 frames / 5 s | At most 5 |
| RBench Common Manipulation | 50 I2V cases | Native aspect, 720-pixel short edge, 24 FPS, 120 frames / 5 s | One real candidate; no quality rerolls |
| PhyGenBench | 160 official T2V prompts | Photographic pixel-space target, 1280×720, 24 FPS, 120 frames / 5 s | At most 5 |
| PCVE-RigidBench | Full 129-case V2V set | 1280×720, 24 FPS, 96 frames / 4 s | At most 5 |

Keep `case-only-v1` isolation and a **1,000,000 reported output-token budget
per real attempt**. Usage received in an in-flight response is charged in
full, including any overshoot. Budget exhaustion is a real failed attempt.
Recorded infrastructure failures, including credential, rate-limit, storage
and transport failures, are quarantined and do not consume model attempts.

The supplied RBench config uses `max_attempts: 1` and `max_total_attempts: 1`.
Later record ordinals are permitted only when every earlier same-case/model
record failed with a positively identified infrastructure error. Directory
ordinals are permanent provenance; an ordinal above one does not itself mean
a second real candidate. RBench stops after its one real attempt and preserves
failure rather than rerolling for quality. The other benchmarks retain a
five-real-attempt cap; do not apply that recovery policy to RBench.

## 3. Admit one real smoke per benchmark

Set `$BENCHMARK_CONFIG` to one of the four local configs and `$SMOKE_CASE` to
its supplied representative case ID:

```bash
pixelworld doctor --config "$BENCHMARK_CONFIG" --require-sandbox
pixelworld run-case --config "$BENCHMARK_CONFIG" \
  --model my-model --track vision-agent --case "$SMOKE_CASE"
```

Inspect the manifest, exact model route, permitted image attachment, native
events/tool results, runnable code, RGB/frame validation, token accounting,
budget enforcement and filesystem/network isolation. Check the relevant
pinned evaluator smoke separately; technical admission does not depend on a
favorable physics score. Preserve all attempts and private raw recordings.

Use the same config/run root for smoke and full matrix so an accepted smoke
is reused. For a separately staged smoke, the owner must supply an explicit
provenance-checked reuse mapping. **Do not regenerate an accepted RBench smoke.**
Record admission results for all four adapters before dispatching full cohorts.

## 4. Launch and monitor each matrix

```bash
pixelworld matrix run --config "$BENCHMARK_CONFIG"
pixelworld matrix status --config "$BENCHMARK_CONFIG"
```

Repeat for each benchmark config under a supervised service with durable
logs. Keep concurrency within the agreed resource budget. No single legacy
launcher should infer the new suite or overwrite completed directories.
Text controls and the Codex/OpenCode/Claude Code
[harness analysis](HARNESS_COMPARISON.md) require their separate frozen
configs and admission checks; they are not default new-model runs. Pi is
historical.

## 5. Evaluate complete scopes

A full benchmark score requires all 198 Physics-IQ, 50 RBench, 160 PhyGenBench
or 129 PCVE accepted outputs, plus matching evaluator provenance. Missing
outputs block a full score. Preserve failed cases; do not duplicate outputs,
drop them from the denominator or invent aggregate scores. Acceptance smokes
and incremental judgments remain labeled as partial evidence.

Follow [AUTOMATIC_EVALUATION.md](AUTOMATIC_EVALUATION.md) and the current suite
for evaluator setup. RBench uses the pinned GPT branch with GPT-6.1-Sol;
PhyGenBench uses the GPT-6.1-Sol three-stage ordinal MCQ adaptation. Retained
older judge versions stay separate. PCVE retains the full native 129-case
editing evaluator. Reference-pool video quality is a separate analysis.

## 6. Return and publish the run

Return a credential-free handoff manifest with repository commit, executable
version/hash, exact model/provider, config/input hashes, case status, recorded
usage and retry provenance, artifact-store URI, evaluation provenance when
complete, and dashboard URL. Preserve private originals and archive receipts
in durable storage.

Use [HF_PUBLISHING.md](HF_PUBLISHING.md) for the sanitized exporter, resumable
case/trace uploader, progress coverage and dataset-first dashboard publisher.
GitHub receives code, templates and protocol/provenance notes. Generated
videos/tensors, benchmark inputs/references, scores, credentials and raw API
archives belong in the designated artifact stores.
