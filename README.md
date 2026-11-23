# PixelWorld

PixelWorld evaluates a **coding agent as a direct RGB pixel world model**. The
agent receives a benchmark prompt (and, for I2V/editing tasks, the permitted
source pixels), writes runnable Python, and emits every output frame itself. The
generation path does not call an image/video generator, learned renderer,
physics engine, or reference continuation.

The harness is built around **OpenAI Codex CLI**. Model identity and API routing
are configuration, so the same cleanroom can run any model exposed by a Codex
installation or an OpenAI Responses-compatible gateway. PixelWorld does not
install, vendor, or replace Codex; it invokes the user's own executable.

## Main-suite protocol update

The new study uses **Physics-IQ, RBench Common Manipulation, PhyGenBench,
and PCVE-RigidBench**. See the [updated protocol and collaborator runbook](docs/MAIN_SUITE_20261124.md)
for the pinned subset, judge adaptations, and admission gates. Existing
RoboJudge results remain in the historical freeze below.

## November 17 historical main-experiment freeze

| Item | Frozen setting |
|---|---|
| Benchmarks | Physics-IQ Verified (198), RoboJudge shared-50 (50), PCVE-RigidBench (129), PhyGenBench T2V (160) |
| Main track | `vision-agent` (paper-facing name: **Vision**) |
| Text track | Separate analysis control for selected models; not required for a main-table row |
| Physics-IQ / PhyGenBench output | 1280×720, 24 FPS, 120 frames / 5 s |
| RoboJudge output | Preserve aspect ratio, 720-pixel short edge, 24 FPS, 120 frames / 5 s |
| PCVE output | 1280×720, 24 FPS, 96 frames / 4 s; official editing timeline |
| Codex | 0.169.0, pinned per cohort |
| Filesystem policy | `case-only-v1` |
| Output-token budget | **1,000,000** reported output tokens per real attempt |
| Attempt cap | At most 5 real model attempts per case |
| Infrastructure failures | Quarantined separately; do not consume model attempts |
| PhyGenBench scoring | GPT-5.5 three-stage MCQ adaptation; VLMs and probability scoring replaced explicitly, not the original PhyGenEval score |

The 1M budget is a 2026-11-17 amendment. Earlier completed deliveries retain
their recorded 100k budget and are reused without regeneration; analyses must
separate attempts by the per-run `output_token_budget` value.

Current confirmed models: GPT-6.1-Sol, GPT-6-Astra, GPT-5.6-Sol,
GPT-5.6-Terra, GPT-5.6-Luna, DeepSeek-V4.1-Flash and Seed-2.1-Pro-0915.
The five analysis models are Sol 6.1, Sol/Terra/Luna 5.6 and DeepSeek.
Additional exact-version models such as Kimi,
GLM or Qwen are appended under the same protocol by changing only the model and
provider configuration.

The authoritative specification is
[docs/FINAL_EXPERIMENT_PROTOCOL.md](docs/FINAL_EXPERIMENT_PROTOCOL.md). The
public benchmark reference is
[docs/MAIN_EXPERIMENT_PROTOCOL.md](docs/MAIN_EXPERIMENT_PROTOCOL.md).

## Start here

- [Current main-suite update and collaborator runbook](docs/MAIN_SUITE_20261124.md)
- [Documentation index](docs/INDEX.md)
- [Historical experiment freeze and analysis settings](docs/FINAL_EXPERIMENT_PROTOCOL.md)
- [Main benchmark reference](docs/MAIN_EXPERIMENT_PROTOCOL.md)
- [Automatic scoring and GPU dispatch](docs/AUTOMATIC_EVALUATION.md)
- [Retained-result provenance and configuration audits](docs/RESULT_PROVENANCE.md)
- [Add a model](docs/ADD_MODEL_RUNBOOK.md)
- [Copy-paste collaborator handoff](docs/COLLABORATOR_HANDOFF.md)
- [Codex integration](docs/CODEX.md)
- [Codex / OpenCode / Claude Code harness comparison and readiness](docs/HARNESS_COMPARISON.md)
- [Filesystem isolation](docs/FILESYSTEM_ISOLATION.md)
- [Agent workspace and task contract](docs/WORKSPACE_AND_TASK.md)
- [Paper framework](docs/FRAMEWORK_FOR_PAPER.md)

## Results and monitoring

Generated videos and RGB tensors are **not** stored in this code repository.
They are published separately:

- Hugging Face dataset:
  [HuggingFriends/PixelWorld](https://huggingface.co/datasets/HuggingFriends/PixelWorld)
  — public catalogue, per-case JSON, tool events, runnable code, token records,
  previews and posters.
- Live dashboard:
  [PixelWorld dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html)
  — live progress, per-model counts and playable candidates.
- Full sanitized trace bundles:
  [Dataset traces](https://huggingface.co/datasets/HuggingFriends/PixelWorld/tree/main/traces)
  — per-benchmark/model archives; uploads continue separately from inference.
- [Analysis progress](https://huggingfriends-pixelworld-dashboard.static.hf.space/analyses.html)
- [Agent → Qwen → I2V smoke](https://huggingfriends-pixelworld-dashboard.static.hf.space/phygen_firstframe_smoke.html)
- Dashboard source: `tools/build_formal_dashboard.py` and
  `tools/build_analysis_dashboard.py`. Scores and generated assets are read
  from external results rather than embedded in code.

The live exporter refreshes independently of uploads. HF `live/` holds current
metadata and per-case details; `data/` retains shared media and bulk snapshots.
Closed case details, their videos and complete sanitized trace archives are
committed together. Unsynced cases use a clearly labeled live preview fallback.
Attempt status and token accounting update independently of transcoding. HF
receives progress in separate background jobs; GitHub holds reproducible code
and protocol/configuration templates. A snapshot timestamp
describes the exported data; it does not certify that every media upload has
finished.

Each published case keeps its playable candidate, poster, `run_manifest.json`,
token accounting, tool events and runnable `generate.py`. Failed attempts keep
their manifest and trace but have no fabricated video.

## Repository layout

```text
pixelworld/            harness, validation, scheduling, media contract
configs/               current model templates and frozen example configs
docs/                  current protocol and operations documentation
docs/archive/2026-11/  superseded protocols and historical experiments
baselines/             frozen native baseline provenance
tools/                 matrix managers, dashboard builders, audit utilities
tests/                 harness regression tests
```

Generated `data/`, `vendor/`, `runs/`, `site/` and `evaluation_outputs/` are
ignored. Benchmark inputs, reference videos, masks and generated outputs are not
committed.

## Quick start

Requirements: Linux, Python 3.11+, Codex CLI 0.169.0, `git`, `ffmpeg`, and `uv`
for official evaluation.

```bash
git clone https://github.com/SiyuanMaCS/PixelWorld.git
cd PixelWorld
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
export PIXELWORLD_CODEX=/absolute/path/to/codex
pixelworld doctor --config configs/benchmark.example.json
```

Run one real Vision case before a matrix:

```bash
pixelworld run-case \
  --config configs/benchmark.example.json \
  --model my-model \
  --track vision-agent \
  --case 0001
```

Then launch the restart-safe matrix:

```bash
pixelworld matrix run --config configs/benchmark.local.json
pixelworld matrix status --config configs/benchmark.local.json
```

## Add a model

1. Copy [configs/collaborator-model.example.json](configs/collaborator-model.example.json)
   to an ignored `configs/*.local.json`.
2. Set the exact versioned model ID and Responses-compatible provider route.
3. Export the API key in the parent shell; never put the value in JSON.
4. Run `pixelworld doctor` and one real Vision smoke per benchmark, checking
   image input, code execution, saving and reported input/output usage.
5. Send the smoke bundle to the benchmark owner for review.
6. Launch the full matrix with `tools/final_experiments.py` only after the
   generation readiness gates are approved.

Do not change benchmark prompts, generation rules, output specifications,
attempt accounting or evaluators to make a particular model submit. Full
instructions: [docs/ADD_MODEL_RUNBOOK.md](docs/ADD_MODEL_RUNBOOK.md).

## What belongs in Git

Evaluator and chart utilities can use `uv pip install -e ".[evaluation]"` in a
separate evaluation environment. Original RoboJudge/PCVE code and checkpoints
remain evaluator-side. `tools/run_gpu_evaluation.py` coordinates free-memory
claims between PixelWorld evaluators without stopping other workloads.

Commit: harness code, tests, protocol documents, config templates and small
provenance records.

Do not commit: API keys or auth files, local absolute paths, generated videos or
RGB tensors, Physics-IQ reference videos/masks, PCVE target videos or evaluator
ground truth, and per-case raw traces unless a bounded sample is explicitly
agreed.

## Legacy

Earlier 256×144/4 FPS protocols, the withdrawn 15-minute wall-clock budget,
superseded smoke reports and historical benchmark plans are retained under
[docs/archive/2026-11/](docs/archive/2026-11/). They are provenance, not current
instructions.

## Upstream attribution

PixelWorld does not redistribute Physics-IQ code or data. Follow the licenses
and terms of:

- [Physics-IQ and Physics-IQ Verified](https://github.com/google-deepmind/physics-IQ-benchmark)
- [Physics-IQ Verified dataset](https://huggingface.co/datasets/Anates-Labs-Research/Physics-IQ-Verified)
- [OpenAI Codex CLI](https://github.com/openai/codex)
