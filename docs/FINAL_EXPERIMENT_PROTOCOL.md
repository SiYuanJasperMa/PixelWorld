# PixelWorld final experiment protocol

Status: **generation protocol frozen; reviewed formal generation is active**.
Evaluator readiness remains a separate gate and no incomplete cohort
is reported as a benchmark score.
This draft supersedes earlier proposed 480p-main, 4K, 60/120-FPS, and
Qwen/Kimi-analysis matrices. Historical experiments and their instructions are
retained with their original metadata. A completed artifact is not automatically
eligible for the final table.

## Model and track matrix

| Model | Main results | Additional analyses |
|---|---|---|
| GPT-6.1-Sol | Vision | Text/Vision; scale and effort; initialization; native refinement |
| DeepSeek-V4.1-Flash | Vision | Text/Vision; scale and effort; initialization; native refinement |
| GPT-5.6-Sol | Vision | Text/Vision; scale and effort; initialization; native refinement |
| GPT-5.6-Terra | Vision | Text/Vision; scale and effort; initialization; native refinement |
| GPT-5.6-Luna | Vision | Text/Vision; scale and effort; initialization; native refinement |
| GPT-6-Astra | Vision | None |
| Seed-2.1-Pro-0915 | Vision | None |

Collaborator models (for example exact Kimi, GLM or Qwen versions) use the same
Vision-only main matrix. Adding a row changes only model/provider configuration;
it does not change prompts, output specs, Codex version, attempt accounting or
evaluators. Record the precise API model ID rather than a family label.

Paper-facing **Vision** corresponds to the internal `vision-agent` track. Text
has neither an attached image nor image-viewing tools; it can read the same
permitted image/video files through code and numeric pixel operations. Vision
receives the applicable initial image and may inspect generated candidates.
The agent chooses its own checking and revision strategy. No mandatory
observation loop, event plan, or first-valid stopping policy is imposed.

All main runs request `high` reasoning effort. Record the native request value,
model ID, catalog snapshot, actual usage, and API acceptance. Equal effort
labels do not imply equal compute across providers.

## Four main benchmarks

| Benchmark | Input | Cases | Main output | Main evaluator |
|---|---|---:|---|---|
| Physics-IQ Verified | Official I2V switch frame + BPP base prompt | 198 | 1280x720, 24 FPS, 120 frames / 5 s | Pinned official Verified score, `final_score_view` |
| RoboJudge shared-50 | Official initial frame + original instruction and documented paper prefix | 50 | Preserve initial aspect ratio; 720-pixel short edge, 24 FPS, 120 frames / 5 s | Original RoboJudge-9B; exact PA/IA aggregation must pass the readiness audit |
| PCVE-RigidBench | Complete factual source video + official quantitative-English intervention | 129 | Original source aspect/timeline; current official set is 1280x720, 24 FPS, 96 frames / 4 s | Official full-129 PES; other metrics separately |
| PhyGenBench T2V | Original 160 official prompts | 160 | 1280x720, 24 FPS, 120 frames / 5 s | PixelWorld GPT-5.5 VLM judge, 8 chronological frames; adapted evaluation |

RoboJudge uses five-second outputs for the selected shared-50 protocol.
PhyGenBench's five seconds are a **PixelWorld generation setting**, not a claim
that its official benchmark mandates this duration. PCVE's frame
indices and four-second timeline are part of its editing contract. Do not turn
PCVE into a five-second continuation task. For a 4:3 RoboJudge image, the
720-short-edge canvas is 960x720; for 16:9 it is 1280x720. Audit every prepared
case rather than assuming all inputs are 16:9.

The main table has models as rows and benchmarks as columns. Video-generation
baselines use their published model/version and actual generation configuration;
they do not inherit PixelWorld's resolution or FPS. Include only matched
published settings, and do not assign one published score to several pricing
tiers. Baselines appear as independent points in cost plots, not scaling lines.
Do not average unrelated benchmark metrics into an unqualified overall score.

The current PhyGenBench score is the PixelWorld GPT-5.5 judge protocol in
`tools/evaluate_phygen_gpt55.py`: uniformly sample 8 frames, resize only judge
images to a maximum edge of 768, score physical consistency from 1 to 5, and
map the mean over all 160 prompts to 0–1 as `(mean - 1) / 4`. Domain means are
reported separately. This does not implement the official hierarchical
PhyGenEval ensemble or its per-case rounding. Published official scores retain
their original metric label; matched video-model reruns use the same PixelWorld
judge as coding agents. Do not claim that these metric scales are interchangeable.

## Additional analyses: five models

The analysis roster is GPT-6.1-Sol, GPT-5.6-Sol, GPT-5.6-Terra,
GPT-5.6-Luna and DeepSeek-V4.1-Flash. Astra and Seed remain main-only.

### Text versus Vision

Same inputs, prompts, output specification, resource allowance, and output-token
limit. Use main Vision results as the common reference and add Text for the five
analysis models. Keep initial attachments distinct from actual inspection-tool
calls. Report actual behavior from trace records.

### Resolution, reasoning effort, and FPS

Physics-IQ only, Vision, a shared five-second task and fixed input image. Use
the same 720p switch frame for an output-only scale comparison. Existing runs
whose input image also changes resolution are an end-to-end resolution study
and must not be silently merged with output-only scaling.

| Condition | Output | Requested/native effort | FPS | Frames |
|---|---|---|---:|---:|
| Main | 720p | Main (requested High) | 24 | 120 |
| Resolution lower | 480p | Main | 24 | 120 |
| Resolution higher | 1080p | Main | 24 | 120 |
| Effort lower | 720p | Requested `low` | 24 | 120 |
| Effort higher | 720p | GPT: requested `xhigh`; DeepSeek: requested `max` | 24 | 120 |
| FPS lower | 720p | Main | 12 | 60 |
| FPS higher | 720p | Main | 48 | 240 |

This is seven conditions per analysis model, not a full factorial grid. The
Main condition is shared and is generated once. An unavailable higher/lower
effort is N/A, not a relabeled duplicate. Neither UI catalogs nor a silently
accepted parameter alone proves that an upstream model changed its reasoning
compute; verify the route and disclose the evidence. The current upper-effort
acceptance probes returned completed Responses with sampled usage for all five
routes. The gateway forwards the requested effort in its native request. This
proves request/API acceptance, not the provider's internal compute allocation;
the paper must describe these as requested-effort conditions.

FPS references: [Lance](https://lance-project.github.io/) documents native
480p/12-FPS video generation; [LTX-2's official documentation](https://ltx.io/blog/how-to-upscale-ai-video-from-30fps-to-60fps)
lists native 24/25/48/50 FPS. The 12/24/48 design supplies half/base/double output
frame counts. Physics-IQ's recordings are 30 FPS; its evaluator matches the
submission FPS by transforming references, including linear interpolation.
The comparison uses a common **24-FPS evaluation grid**. At each evaluation
timestamp select the existing source frame displayed at that time (zero-order
hold). Thus 48-FPS output selects every second frame; 12-FPS output repeats
each source frame twice. This is evaluator-side display normalization, not
extra generated RGB. Preserve raw 60/120/240-frame artifacts, and price/bubble
size them using their real output count. The official evaluator and its
24-FPS references remain unchanged. Do not claim unmeasured native 48-FPS
ground truth. Native playback smoothness and reference matching answer
different questions. The normalization exporter still needs its smoke audit.

The cost/score figure uses measured token usage and recorded cached input.
Use verified model-specific tariffs where available. Served proxy aliases without
a verified tariff use an explicitly labeled shared reference tariff, rather than
an alleged observed bill. Keep 70% and 80% cache assumptions as sensitivity
scenarios; missing cache counters remain unknown and exclude full-cohort cost
claims. Report whether the denominator is an accepted delivery or includes
failed-attempt overhead. Bubble area is proportional
to width x height x number of generated frames. Reference-tariff scenarios and
fictional mockup scores are layout aids only, kept outside results data.

### PhyGenBench paired initialization

Use the same original physical prompt in four conditions: T2V-Text, T2V-Vision,
I2V-Text, I2V-Vision. Output is 720p/24 FPS. The extension constructs its scene
through **agent pixel first frame → Qwen-Image-2.1 appearance re-render → agent
I2V**. Qwen receives the agent's scene image as conditioning, rather than
constructing an unrelated scene solely from a rewritten caption. It is permitted
only as a separate initial-image preparation stage, never for video continuation. Preserve
image candidates, selection criteria and hashes; confirm the selected image
depicts the pre-action state. Report I2V as a PixelWorld extension rather than
an official PhyGenBench T2V leaderboard result. Reuse the two T2V conditions.

### Native checking/refinement and qualitative diagnosis

Read saved candidates and code/usage/tool logs without changing agent behavior.
Distinguish within-run revision from a fresh failed-run retry. Different code
hashes may yield identical RGB; a different RGB hash is a change, not evidence
of improvement. Separate appearance/segmentation, motion/interaction, and
delivery-format changes. Compare first-valid and final candidates on matched
tasks, and report additional consumption after the first candidate without
calling all of it refinement cost.

Failure-rate/model-specific non-delivery anatomy belongs in the appendix; main
figures still disclose coverage. Trace explanations are qualitative mechanisms
supported by descriptive statistics, not a causal proof that extra calls help.
PhysBench understanding on a frozen subset remains a separate optional analysis
preparation; its subset and sampling contract must be frozen before launch.

## Common generation contract

The agent receives a clean workspace, task text, permitted visual inputs, a
media specification, the shared saving helper, and the player template. It must
write runnable `generate.py`, run it, and call `artifact_io.save(...)` at least
once. Saved outputs must satisfy the media contract. If revised, each candidate
is saved before continuing. Use the approved efficiency sentence:

> Aim for high-quality output while considering efficiency.

Retain `generate.py` so it regenerates the submitted RGB video. The generation
path directly computes RGB; it cannot invoke a physics engine, scene renderer,
learned renderer, or external image/video generator. Mathematical pixel/motion
operations remain available. No evaluator, reference continuation, mask, target
trajectory, or benchmark score is exposed to the agent.

## Runtime and accounting

- Codex is pinned to **0.169.0**, with executable SHA-256 and runtime manifest.
  No automatic CLI upgrade during a cohort.
- Formal configs select **`filesystem_policy: case-only-v1`**: native folder
  permissions expose the case workspace and required runtime libraries, disable
  tool networking, and keep other runs/evaluation/archive directories outside
  readable roots. Freeze the effective profile/hash per attempt and verify shell
  and image tools plus a real generation before matrix launch. Historical
  `workspace-write` configurations retain their recorded execution boundary.
- Executables, workspaces, `CODEX_HOME`, caches, temporary files, logs and
  artifacts reside under the cloud workspace. Resolve symlinks when auditing
  destinations; a cloud-looking symlink into a worker's root disk is not allowed.
- Worker SSH supplies compute; every process has an explicit cloud working
  directory. Use existing dependencies packaged/locked for the runtime.
- Cap concurrent jobs and scientific-library threads. Measure per-process-tree
  peak RSS in smoke before finalizing main/1080p memory allowances. Host-wide
  free memory/load is not the same as the worker's cgroup allocation.
- Each model attempt gets **1,000,000 reported output tokens**, enforced externally
  without mandatory tool-count or revision-count limits. Reasoning already
  included in provider output is not added again. API-request granularity can
  produce recorded overshoot; log the observed limit and overshoot.
- At most **five actual model attempts per case/track**. Infrastructure episodes
  are quarantined separately and do not consume this cap. Budget exhaustion,
  ending without submission and invalid submission do consume it.
- Retry directory ordinals, authorization windows and model-attempt counts are
  distinct. Formal configs must give each real attempt a fresh token allowance;
  an old shared multi-attempt budget window must not silently reduce later runs.
- Queue/rate-limit waiting stays queued and is not judged as model failure.
  Persist activity/heartbeat and usage evidence; recover interruptions without
  duplicate live workers or duplicated tool execution.
- Per-request usage, terminal usage, cache usage, visible reasoning, tool results,
  code revisions and candidate versions are retained for analysis and potential
  agentic SFT. Missing usage is unknown, not zero. Training eligibility is separate
  from successful artifact delivery.

## Reuse and review gates

Reuse completed matching runs; do not rerun merely to rename a cohort. Match
case, model/version, track, input/prompt hashes, generation rules, output spec,
effort and budget protocol, and preserve original environment provenance.
Legacy-v1 or mismatched scale runs remain explicitly labeled references.

Before full launch, present: exact configs and prompts, environment/CLI hashes,
resource smoke measurements, every registered model route, generation/evaluator smoke
evidence, repeat/infra accounting tests, cloud-only storage checks, and live
dashboard screenshots/URLs. New full cohorts require the owner's review; the
current main cohort has already been authorized. The
dashboard must distinguish preparing, smoke, ready-but-paused, queued, active,
stale, evaluating and complete using current evidence rather than old PID files.

### Reusing compatible historical deliveries

A filesystem isolation improvement alone does not require regenerating an
otherwise compatible delivery. `tools/audit_reuse.py --compatible-history`
checks original model identity, track, prompt and conditioning hashes, supplied
video-editing inputs, media contract, generation rules, Codex version, effort,
recorded per-attempt budget, attempt cap and saved artifacts. It creates an external
`reuse_registry.json`; the supervisor resolves the original successful source
and skips future dispatch for that model/track/case. Original manifests, code,
RGB arrays, timestamps and attempt numbers remain unchanged. A changed source
manifest invalidates the audited reference. Publication uses distinct source
identities to avoid collisions with newer attempts, marks historical provenance,
and computes token/duration means from the selected delivery only.

PCVE's earlier 256×144 / 24 FPS / 50k cohort remains a historical cohort; it is
not a 720p main result. PhyGen runs without the current photographic target
remain separate when their media contract or task rules differ. Reuse is decided
by artifacts and metadata, not by a directory's display name.

### Budget amendment 2026-11-17

All models in the four main benchmarks now receive **1,000,000 reported output
tokens per real attempt** instead of 100,000. The reason is empirical: several
models exhausted 100k at 720p/24 FPS while still in analysis and produced no
artifact. Completed deliveries made under the earlier 100k allowance are
retained unchanged and are not regenerated; each manifest keeps its original
`output_token_budget`. Analyses must therefore separate 100k and 1M attempts by
the recorded per-run value rather than assuming one budget for the whole cohort.
