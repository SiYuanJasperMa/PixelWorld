# PixelWorld

PixelWorld evaluates a **coding agent as a direct RGB pixel world model**.
Given a benchmark prompt and the permitted source pixels for I2V or editing,
the agent writes runnable Python and emits every output frame. The generation
path uses no image/video generator, learned renderer, physics engine or
reference continuation.

[Code](https://github.com/SiYuanJasperMa/PixelWorld) ·
[HF dataset](https://huggingface.co/datasets/HuggingFriends/PixelWorld) ·
[Live dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html) ·
[Documentation](docs/INDEX.md)

## Current main suite

The authoritative current protocol is
[Main suite, November 24](docs/MAIN_SUITE_20261124.md).

| Benchmark | Scope | Output contract |
|---|---|---|
| Physics-IQ Verified | 198 I2V cases | 1280×720, 24 FPS, 120 frames / 5 s |
| RBench Common Manipulation | 50 I2V cases | Native aspect ratio, 720-pixel short edge, 24 FPS, 120 frames / 5 s |
| PhyGenBench | 160 official T2V prompts | 1280×720, 24 FPS, 120 frames / 5 s; photographic pixel-space target |
| PCVE-RigidBench | Full 129-case V2V editing set | 1280×720, 24 FPS, 96 frames / 4 s; official editing timeline |

The main track is `vision-agent` (paper name: **Vision**). Text is a separate
analysis control; Vision-Input is retained for explicit legacy configurations.
Codex CLI **0.169.0**, `case-only-v1` isolation and requested high reasoning
effort are pinned. Each real attempt has a **1,000,000 reported output-token**
budget. The general cap is five real attempts per case; **RBench permits one
real candidate per sample, with no quality rerolls or Best-of-N**.
Infrastructure retries are recorded separately and do not spend these caps.

The nine-model roster is GPT-6.2-Sol, GPT-6.1-Sol, GPT-6-Astra, GPT-5.6-Sol,
GPT-5.6-Terra, GPT-5.6-Luna, DeepSeek-V4.1-Flash, Seed-2.1-Pro-0915 and GLM-5.4.
Inclusion in the roster does not imply a completed cohort. The dashboard
reports generation, evaluation and publication progress separately.

The dashboard also lists the **native GPT-6.2-Astra cohort** separately as
`gpt62-astra-native`. It uses the same benchmark media contracts with native
OpenAI authentication, **Codex 0.172.0 / high / default service tier**. This is
an explicit version exception; do not pool it with the 0.169.0 harness baseline
or identify it as the TRAEX GPT-6.2-Sol route. Its completed quota study has
41 accepted runs, preserved original audit evidence, and
[per-run token and quota records](https://huggingface.co/datasets/HuggingFriends/PixelWorld/blob/main/metadata/astra62_native_quota_final.json).
Further native generation awaits the owner's budget decision.

RBench uses the pinned upstream Common Manipulation GPT branch adapted to
GPT-6.1-Sol. PhyGenBench uses the three-stage ordinal MCQ adaptation with
GPT-6.1-Sol; older GPT-5.5 judgments remain separate. Complete benchmark scopes
and matching evaluator provenance are required for full scores. Video-quality
analysis is separate from physical QA.

The [harness analysis](docs/HARNESS_COMPARISON.md) compares Codex 0.169.0,
OpenCode 1.19.6 and Claude Code 2.1.304 under the same controlled generation
boundary. Adapter admission and cohort completion are recorded independently.
Pi evidence and RoboJudge cohorts are historical. The
[November 17 freeze](docs/FINAL_EXPERIMENT_PROTOCOL.md) documents earlier
settings, including retained 100k-budget deliveries; it is not the current
four-benchmark launch specification.

## Retained analyses

The analysis roster includes GPT-6.1-Sol, GPT-5.6-Sol, GPT-5.6-Terra,
GPT-5.6-Luna, DeepSeek-V4.1-Flash and **GPT-6.2-Sol**. A prepared config or a
queued condition is not proof that generation or scoring has completed.

| Analysis | Frozen comparison |
|---|---|
| Text versus Vision | Physics-IQ, PhyGenBench and PCVE; reuse the main Vision cohort |
| Spatial resolution | Physics-IQ: 480p / 720p / 1080p, fixed 24 FPS |
| Temporal sampling | Physics-IQ: 12 / 24 / 48 FPS, fixed 720p and five-second duration |
| Thinking effort | Physics-IQ: low / high / the model's verified higher or maximum setting |
| Agent harness | Physics-IQ: Codex 0.169.0 / OpenCode 1.19.6 / Claude Code 2.1.304, the same model and 720p/24 FPS contract |
| Video quality | Separate GPT-6.1-Sol appearance judgments and benchmark-specific FVD |

RBench currently has its main Vision cohort; no Text or Vision-Input control
has been admitted. Main 720p/24 FPS/high results are reused in the appropriate
analyses. Physical QA, subjective appearance and FVD have separate score
versions and scopes. Interrupted transport calls are not evidence of poor
generation quality; preserve their original records and correct accounting
only when the interruption is evidenced. Pi, DSH, retired Flash conditions and
superseded quality settings remain historical.

## Run or add a model

Requirements: Linux, Python 3.11+, Codex CLI 0.169.0, `git`, `ffmpeg`, `uv`,
and a working case sandbox.

```bash
git clone https://github.com/SiYuanJasperMa/PixelWorld.git
cd PixelWorld
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
export PIXELWORLD_CODEX="$(command -v codex)"
```

Obtain the frozen per-benchmark configs and licensed input manifests from the
benchmark owner. Set `$BENCHMARK_CONFIG` to an ignored local config and
`$SMOKE_CASE` to an actual case ID in that config. Generic example configs are
interface examples and do not freeze the current suite.

```bash
pixelworld doctor --config "$BENCHMARK_CONFIG" --require-sandbox
pixelworld run-case --config "$BENCHMARK_CONFIG" \
  --model my-model --track vision-agent --case "$SMOKE_CASE"
# After checking the delivery and recording the admission gates:
pixelworld matrix run --config "$BENCHMARK_CONFIG"
pixelworld matrix status --config "$BENCHMARK_CONFIG"
```

Repeat with each of the four frozen configs. Keep the accepted smoke under the
same configuration/run root so the matrix reuses it, especially for RBench.
The current launch workflow is in [Add a model](docs/ADD_MODEL_RUNBOOK.md) and
the [copy-paste handoff](docs/COLLABORATOR_HANDOFF.md).
The two collaborator assignments are [Claude main experiments](docs/RUN_CLAUDE_MAIN.md)
and [Kimi + Qwen main experiments](docs/RUN_KIMI_QWEN_MAIN.md): both cover only
the four full Vision main benchmarks, not the analysis matrix.
`tools/final_experiments.py` retains the older RoboJudge suite mapping and must
not be used to launch this four-benchmark suite.

## Results and publication

GitHub holds code, protocol documents, config templates and small provenance
records. Generated outputs are published to
[HuggingFriends/PixelWorld](https://huggingface.co/datasets/HuggingFriends/PixelWorld).
The [dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html)
shows playable candidates and progress. See also
[analysis progress](https://huggingfriends-pixelworld-dashboard.static.hf.space/analyses.html).

The dataset's `metadata/experiment_index.json` indexes the current suite;
`metadata/main_suite_status.json` and `dashboard/index.html` are published in
one commit and verified at that revision before the Space snapshot is updated.
An open dashboard tab polls the dataset every 60 seconds and replaces its main
content when a matching newer snapshot is available. The snapshot publisher
checks for content changes every five minutes.

Per-case details and sanitized full traces live under
`live/<suite>/<model>/<track>/`; preview media lives under `data/assets/`.
Older `data/` snapshots and `traces/` archives remain available for provenance.
Public traces retain recorded native events, images, code and input/output
text after sanitization. Licensed standalone benchmark inputs, evaluator
references and private raw API archives are excluded.

Publication coverage at `metadata/publication_coverage.json` compares current
case hashes with completed upload receipts. It does not certify a full remote
byte audit. Generation completion, a fresh status timestamp and complete media
publication are different states. See [HF publishing](docs/HF_PUBLISHING.md)
for the exporter, resumable uploader, progress watcher and verification steps.

## Repository layout

```text
pixelworld/            harness, validation, scheduling, media contract
configs/               model templates and frozen example configs
docs/                  protocols and operational documentation
docs/archive/          superseded experiments and provenance
baselines/             native baseline provenance
tools/                 dashboard, publication, evaluation and audit utilities
tests/                 harness regression tests
```

Generated `data/`, `vendor/`, `runs/`, `site/` and `evaluation_outputs/` are
ignored. Keep credentials, private endpoints, local absolute paths, generated
videos/tensors, benchmark inputs, references/masks and scores out of Git.
Durable private storage holds original run artifacts and raw API recordings;
see [storage layout](docs/STORAGE_LAYOUT.md),
[storage policy](docs/STORAGE_POLICY.md) and
[recording contract](docs/DATA_RECORDING_CONTRACT.md).

## Upstream attribution

Follow the upstream licenses and data terms:

- [Physics-IQ](https://github.com/google-deepmind/physics-IQ-benchmark) and
  [Verified dataset](https://huggingface.co/datasets/Anates-Labs-Research/Physics-IQ-Verified)
- [RBench](https://huggingface.co/datasets/DAGroup-PKU/RBench) and
  [ReVidgen](https://github.com/DAGroup-PKU/ReVidgen)
- [PhyGenBench](https://github.com/OpenGVLab/PhyGenBench)
- [PCVE-RigidBench dataset](https://huggingface.co/datasets/ccmoony/PCVE-RigidBench)
- [OpenAI Codex CLI](https://github.com/openai/codex)
