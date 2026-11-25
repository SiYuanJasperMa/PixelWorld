# PixelWorld

PixelWorld evaluates coding agents that write runnable programs and directly
output RGB video frames. The generation path uses no image/video generator,
learned renderer, physics engine or reference continuation.

[Code](https://github.com/SiYuanJasperMa/PixelWorld) ·
[Dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html) ·
[Videos and traces](https://huggingface.co/datasets/HuggingFriends/PixelWorld) ·
[Documentation](docs/INDEX.md)

## Main experiments

| Benchmark | Task | Full scope | Main setting | Physical score |
|---|---|---:|---|---|
| Physics-IQ Verified | I2V | 198 cases | 1280×720 · 24 FPS · 5 s | Official Verified |
| RBench Common Manipulation | I2V | 50 cases | Native aspect, short edge 720 · 24 FPS · 5 s | Official GPT branch, GPT-6.1-Sol adaptation |
| PhyGenBench | T2V | 160 prompts | 1280×720 · 24 FPS · 5 s | GPT-6.1-Sol three-stage MCQ adaptation |
| PCVE-RigidBench | V2V editing | 129 cases | 1280×720 · 24 FPS · 4 s | Official PES |

The main track is **Vision** (`vision-agent`). Text is an analysis control.
The harness uses **Codex 0.169.0**, `case-only-v1` isolation, requested high
reasoning and **1M reported output tokens per real attempt**. The general
allowance is five real attempts; RBench's original allowance is one. Recorded
infrastructure failures are exempt. Owner-authorized sparse-tail recovery is
tracked separately and stops at the first valid delivery; successful videos
are never rerolled for a better score. See [retry accounting](docs/RETRY_POLICY.md).

The main roster is GPT-6.2-Sol, GPT-6.1-Sol, GPT-5.6-Sol,
GPT-5.6-Terra, GPT-5.6-Luna, DeepSeek-V4.1-Flash, Seed-2.1-Pro-0915 and GLM-5.4.
The dashboard reports their actual generation, evaluation and publication
coverage. Native GPT-6.2-Astra is a separate cohort using Codex 0.172.0; its
version and quota records are kept separate from the main harness. Historical
GPT-6-Astra runs are retained in legacy results and excluded from the paper main
table.

The authoritative scope, inputs and evaluator pins are in
[the current main protocol](docs/MAIN_SUITE_20261124.md). Physical scores,
GPT-6.1-Sol photographic-appearance scores and FVD are separate evaluations.
PhyGenBench's MCQ adaptation is not the original probability-based metric.

## Run a model

Requirements: Linux, Python 3.11+, Codex 0.169.0, `git`, `ffmpeg`, `uv` and
working case isolation.

```bash
git clone https://github.com/SiYuanJasperMa/PixelWorld.git
cd PixelWorld
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
export PIXELWORLD_CODEX="$(command -v codex)"
```

Obtain the four frozen configs and permitted input manifests from the owner.
Set `$BENCHMARK_CONFIG`, `$MODEL_KEY` and `$SMOKE_CASE` to that supplied setup;
[repository templates](configs/README.md) do not include licensed inputs.

```bash
pixelworld doctor --config "$BENCHMARK_CONFIG" --require-sandbox
pixelworld run-case --config "$BENCHMARK_CONFIG"   --model "$MODEL_KEY" --track vision-agent --case "$SMOKE_CASE"
# After validating the smoke, reuse it in the full matrix:
pixelworld matrix run --config "$BENCHMARK_CONFIG"
pixelworld matrix status --config "$BENCHMARK_CONFIG"
```

| Assignment | Instructions |
|---|---|
| Claude model: four full Vision benchmarks, 537 cases | [Claude main experiments](docs/RUN_CLAUDE_MAIN.md) |
| Kimi and Qwen: four full Vision benchmarks each, 1,074 tasks | [Kimi + Qwen main experiments](docs/RUN_KIMI_QWEN_MAIN.md) |
| Another exact model/version | [Add-model runbook](docs/ADD_MODEL_RUNBOOK.md) · [Handoff template](docs/COLLABORATOR_HANDOFF.md) |

These assignments use the Codex harness. Claude Code is a separate harness
analysis. Retain all attempts, reuse compatible accepted videos and record
missing usage as unavailable. Do not feed evaluator results into generation.

## Analysis experiments

| Analysis | Comparison |
|---|---|
| Text versus Vision | Physics-IQ, PhyGenBench and PCVE |
| Resolution | Physics-IQ: 480p / 720p / 1080p, fixed 24 FPS |
| FPS | Physics-IQ: 12 / 24 / 48 FPS, fixed 720p and five seconds |
| Thinking effort | Physics-IQ: low / high / verified higher or maximum setting |
| Harness | Physics-IQ: Codex 0.169.0 / OpenCode 1.19.6 / Claude Code 2.1.304 |
| Video quality | Separate GPT-6.1-Sol appearance judgments and reference-pool FVD |

The analysis roster includes GPT-6.1-Sol, GPT-5.6-Sol, GPT-5.6-Terra,
GPT-5.6-Luna, DeepSeek-V4.1-Flash and GPT-6.2-Sol. Compatible main results are
reused. See [harness comparison](docs/HARNESS_COMPARISON.md) and
[analysis progress](https://huggingfriends-pixelworld-dashboard.static.hf.space/analyses.html).

## Results, storage and publishing

Standard TRAE API bridges share a 64 GiB log-directory capacity and a 20 GiB
filesystem reserve across models, including when startup flags are omitted.
Check `/ready` before dispatch; `/health` alone proves only that the listener is
alive. See the [storage policy](docs/STORAGE_POLICY.md) and
[provider recovery guide](docs/PROVIDER_FAILURE_RECOVERY.md).

| Location | Contents |
|---|---|
| [GitHub](https://github.com/SiYuanJasperMa/PixelWorld) | Code, protocol documents and portable templates; all future source updates use this repository |
| [HF dataset](https://huggingface.co/datasets/HuggingFriends/PixelWorld) | Generated previews, sanitized complete traces, case indices, scores and publication receipts |
| [HF dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html) | Live main matrix, analysis progress, videos and trace browser |
| Private shared storage | Original API inputs/outputs, full artifacts, exact configs and verified run archives |

The main snapshot publisher checks every minute. Open tabs poll HF every
60 seconds; generation, scoring and upload coverage are shown separately.
See [HF publishing](docs/HF_PUBLISHING.md), [storage layout](docs/STORAGE_LAYOUT.md)
and [repository/publication ownership](docs/REPOSITORY_AND_PUBLICATION.md).

## Repository map

| Directory | Purpose |
|---|---|
| `pixelworld/` | Harness, media contracts, validation, isolation and scheduling |
| `configs/` | Portable interface templates; frozen runtime configs are owner-supplied |
| `tools/` | [Evaluation, monitoring, publication and archive utilities](tools/README.md) |
| `tests/` | Harness and publication regression checks |
| `docs/` | [Current protocols and operating guides](docs/INDEX.md) |
| `docs/archive/` | Superseded experiments and historical provenance |
| `baselines/` | Native video-model baseline provenance |

Generated data, run roots, checkpoints and site outputs are ignored. Credentials,
private paths, standalone benchmark references, masks and raw API archives must
stay out of Git. Historical RoboJudge, Pi/DSH and Flash work is archived;
`tools/final_experiments.py` and the old Pages publisher are legacy utilities.

Code is [Apache-2.0](LICENSE). Follow [upstream attribution](NOTICE) and the
licenses for [Physics-IQ](https://github.com/google-deepmind/physics-IQ-benchmark),
[RBench/ReVidgen](https://github.com/DAGroup-PKU/ReVidgen),
[PhyGenBench](https://github.com/OpenGVLab/PhyGenBench),
[PCVE-RigidBench](https://huggingface.co/datasets/ccmoony/PCVE-RigidBench) and
[Codex](https://github.com/openai/codex).
