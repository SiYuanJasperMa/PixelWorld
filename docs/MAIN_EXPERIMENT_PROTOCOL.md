# PixelWorld main experiment protocol

This document is the single public reference for the current PixelWorld
benchmark harness. It separates the common cleanroom from benchmark-specific
adapters, so new models can be added without changing the experimental
contract.

## Main model cohort

The current cohort is:

| Key | Display name | Codex model id |
|---|---|---|
| `gpt61-sol` | GPT-6.1-Sol | `traex/GPT-6.1-Sol` |
| `gpt56-sol` | GPT-5.6-Sol | `traex/GPT-5.6-Sol` |
| `gpt6-astra` | GPT-6-Astra | `traex/GPT-6-Astra` |
| `deepseek-v41` | DeepSeek-V4.1-Flash | `traex/DeepSeek-V4.1-Flash` |
| `seed21-pro` | Seed-2.1-Pro-0915 | `traex/Seed-2.1-Pro-0915` |

Gemini-3.8-Flash was an experimental add-on and is no longer part of the active
full-run cohort or public monitoring pages. Additional exact-version models
such as Kimi, GLM and Qwen may be appended by collaborators under this same
protocol. Their concrete API IDs must be recorded; family names alone are not
model identities.

All runs request `reasoning_effort="high"` through Codex. This is a requested
harness setting, not a claim that every provider spends equivalent compute.
Actual input/output/reasoning tokens are recorded from provider reports.

## Main track and analysis control

| Track | Image attachment | Visual inspection tools | Purpose |
|---|---:|---:|---|
| `vision-agent` | Yes | Enabled | **Main experiment (paper-facing: Vision).** The agent may use its own multimodal inspection on provided/generated images. |
| `text` | No | Disabled | Separate analysis control for selected free models; not required for every main-table model. |

The paper-facing name for `vision-agent` is **Vision**. Historical
`vision-input` runs are legacy only and are not part of this main protocol.

## Common cleanroom

Each case runs in a fresh Codex workspace with only the benchmark input files,
shared PixelWorld helpers and the task/rules files. Network tools, memory,
subagents, other benchmark cases, evaluator scores, masks, reference
continuations and external image/video generation are unavailable to the
generation agent.

The agent must write runnable `generate.py` and save an explicit RGB tensor.
All stable candidate saves, code versions, stdout/stderr events, tool calls,
token usage and validation results are retained. The final submitted video is
selected by validation and earliest accepted submission, never by evaluator
score.

The active budget policy is:

- at most **5 total model attempts** per case;
- 1M cumulative reported output tokens per attempt window (amended 2026-11-17; earlier 100k deliveries retain their recorded budget);
- `token_budget_exhausted` counts as a model failed attempt;
- provider/key/rate-limit/payload/stream failures are infrastructure failures,
  quarantined separately, and do not count as model failed attempts.

## Main benchmark set: 4 + 1

| Benchmark | Type | Scope | Output spec | Evaluation status |
|---|---|---:|---|---|
| Physics-IQ Verified | I2V physical continuation | 198 official BPP/I2V cases per model | 1280x720, 24 FPS, 120 frames / 5 s | Pinned official Verified evaluator after complete 198-case cohort. |
| RoboJudge shared-50 | I2V embodied manipulation | 50 shared prompts per model | Preserve aspect ratio with 720-pixel short edge, 24 FPS, 120 frames / 5 s | Original RoboJudge-9B path after complete 50-case cohort. |
| PCVE-RigidBench | Physical video editing | 129 official quantitative-English edits per model | Original source aspect/timeline; current set is 1280x720, 24 FPS, 96 frames / 4 s | Official PCVE metrics after complete 129-case cohort. |
| PhyGenBench official T2V | Text-to-video physical concept generation | 160 official prompts per model | 1280x720, 24 FPS, 120 frames / 5 s; direct pixel-space photographic target | Official PhyGenBench scoring after complete 160-prompt T2V cohort. |
| + PhyGenBench I2V extension | Paired initialization analysis | Same 160 prompts, with selected pre-action Qwen-Image-2.1 initial frames | Same 1280x720, 24 FPS, 120-frame output; frame 0 matches the selected initial image | Extra analysis, not an official T2V leaderboard row. |

The shorthand is **4+1**: four official benchmark axes
(Physics-IQ, RoboJudge, PCVE-RigidBench, PhyGenBench T2V) plus one paired
I2V extension for PhyGenBench. VBench-2.0, OpenVE, WorldArena, WorldScore,
WBench and older experiments are archived or candidate studies unless this
document is updated.

## Benchmark adapters

Physics-IQ uses the same official 198 switch-frame inputs across several
scheduler config files. Those files are an implementation detail for resuming
different model cohorts; paper tables should merge them into one Physics-IQ
benchmark.

RoboJudge uses the shared-50 prompt set and keeps PA/IA-style scoring separate.
The generator receives only first frame and task text; RoboJudge-9B and its
checkpoint are evaluator-side only.

PCVE receives the source video and edit instruction, but not target videos,
physical-difference annotations, masks or ground-truth trajectories. The
intervention frame convention is preserved from the official dataset.

PhyGenBench has a formal T2V track using the original 160 prompts and official
scoring. PixelWorld additionally creates an I2V condition by pairing the same
prompt with a selected pre-action Qwen-Image-2.1 initial frame. Qwen is never
used for video continuation. The I2V condition is analyzed as a controlled
extension: it asks how much a high-quality initial image changes the direct RGB
coding agent's generation boundary.

## Public monitoring

Main entry:

- https://siyuanmacs.github.io/pixelworld/index.html

Benchmark pages:

- Physics-IQ: https://siyuanmacs.github.io/pixelworld/physics_iq_baseline.html
- RoboJudge: https://siyuanmacs.github.io/pixelworld/robojudge.html
- PCVE-RigidBench: https://siyuanmacs.github.io/pixelworld/pcve_rigid.html
- PhyGenBench T2V and paired I2V: https://siyuanmacs.github.io/pixelworld/phygen_pixelspace.html

Each page separates valid videos, running cases, pending cases, model
non-delivery, and quarantined infrastructure/recovery attempts.

## Reviewer-facing notes

The output FPS and resolution are frozen before generation, not retrofitted.
Physics-IQ and PhyGenBench use 720p/24 FPS for five seconds; RoboJudge uses a
720-pixel short edge at 24 FPS for five seconds; PCVE keeps its official
four-second, 96-frame, 24-FPS editing timeline.

Do not report a complete official score for a model/track until the required
case count for that benchmark is delivered and the pinned evaluator has run.
Delivery failures are part of the result and remain visible in the trace.
