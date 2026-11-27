# PixelWorld experiment runbook

This is the operational entry point for agents and collaborators. On the owner's
shared workspace, start with `EXPERIMENT_HANDOFF.local.md` at repository root:
it supplies exact config paths, storage locations, worker addresses, current
pause decisions and the last checked progress. Those private files are ignored
by Git. This document supplies the portable protocol and workflow.

Latest user instructions override older plans. After a host reboot, inspect
owners before launching services. Saved `running` counters and PID files alone
do not prove that a task is still executing.

## 1. Experiment scope

| Benchmark | Mode | Cases per model | Main media | Physical evaluation |
|---|---|---:|---|---|
| Physics-IQ Verified | I2V | 198 | 1280×720, 24 FPS, 5 s | Pinned Verified evaluator |
| RBench Common Manipulation | I2V | 50 | Native aspect, short edge 720, 24 FPS, 5 s | Pinned official GPT branch, GPT-6.1-Sol adapter |
| PhyGenBench | T2V | 160 | 1280×720, 24 FPS, 5 s | Three-stage GPT-6.1-Sol MCQ adapter |
| PCVE-RigidBench | V2V editing | 129 | 1280×720, 24 FPS, 4 s | Pinned official PES and supplementary metrics |

PCVE's 129-case main scope includes DELETE cases. The separate removal-only
Table 2 analysis is omitted. RBench reports `(raw_score - 1) / 4` from the
official GPT branch; do not label it as the combined GPT+Qwen leaderboard score.
PhyGenBench's VLM/MCQ replacement is explicitly an adapted score.

Main generation uses `vision-agent`. For T2V, this enables inspection of outputs
without supplying an initial image. Text controls provide no visual attachment
and no output-image tool. Main models are GPT-6.1-Sol, GPT-5.6-Sol/Terra/Luna,
DeepSeek-V4.1-Flash, GPT-6.2-Sol, the separately tagged native GPT-6.2-Astra
cohort, and Seed-2.1-Pro-0915 with its recorded attempt/failure outcomes.
Collaborator Claude/Kimi/Qwen assignments add only the four main Vision cohorts.

| Retained analysis | Cases per model | Comparison |
|---|---:|---|
| Physics-IQ Text | 198 | Text versus main Vision |
| Physics-IQ 480p / 1080p | 198 each | 480p / reused 720p / 1080p, fixed 24 FPS |
| Physics-IQ 12 / 48 FPS | 198 each | 12 / reused 24 / 48 FPS, fixed 720p and 5 s |
| Physics-IQ thinking | 198 per added setting | Low / reused high / verified higher setting |
| RBench Text | 50 | Text versus main Vision |
| PhyGenBench Text | 160 | Output inspection versus Text, both T2V |
| PCVE Text | 129 | Text versus main Vision |

The analysis roster is GPT-6.1-Sol, GPT-5.6-Sol/Terra/Luna,
DeepSeek-V4.1-Flash and GPT-6.2-Sol: 10 added conditions, 10,350 videos.
Native Astra analysis is cancelled. OpenCode 1.19.6 and Claude Code 2.1.304
comparisons are separate, owner-controlled cohorts; check their current pause
decision before dispatch. Flash, Pi, RoboJudge and discarded initialization
routes are historical. Keep the retained successful GPT-6.1-Sol Vision 4K subset
as provenance; it is not a new matrix to launch.

## 2. Frozen generation and retry rules

- The program directly computes RGB frames. No image/video generator, learned
  renderer, reference continuation or physics engine belongs in generation.
- Standard Codex is 0.169.0. Native GPT-6.2 runs use the recorded 0.172.0 wrapper;
  preserve this execution provenance rather than merging harness versions.
- Each real model attempt has a 1M reported output-token budget. Standard
  allowance is five real attempts; RBench's original allowance is one.
- Owner-authorized recovery is recorded separately and stops at the first valid
  delivery. Native continuation configs can carry a larger recovery cap; it is
  not a change to the normal protocol. Directory attempt ordinals are not counts
  of real model attempts. Infra failures do not consume the model allowance.
- Preserve every retained failure, raw message, code revision and candidate.
  Compatible successful results are reused. Do not reroll successes for quality.
- Keep exact task text, input hashes, media contracts and evaluator/dataset pins.
  Do not edit `pixelworld/constants.py` to fix a routing or storage problem.

Current minimal v2 has no mandatory observation/event-plan/method report.
Historical v1 task assets and their recorded contracts remain unchanged.
Agent workspaces are case-isolated; private logs, other experiments, references,
masks, annotations and scores remain outside the visible workspace. Workspace
`.tmp/` is writable scratch. An empty host `/tmp` is not a private namespace.

## 3. One read-only status command

The owner supplies `runtime.local.json`. It registers one exact config per
model/condition, the owning host and private artifact locations.

```bash
python -m tools.ops.status --map runtime.local.json
python -m tools.ops.status --map runtime.local.json --json --output "$STATUS_REPORT"
```

Verify CPU-owned processes **on the CPU worker**:

```bash
python -m tools.ops.status --map runtime.local.json --role cpu --verify-processes
```

The report keeps snapshot counts, snapshot age, actual local case processes and
dispatcher ownership separate. It never launches work, calls a model, retries,
scores, uploads or modifies generation records. A main scope marked complete in
a snapshot still needs artifact/provenance verification before paper reporting.
`exhausted` means a retry window ended, not a confirmed model failure.

## 4. Recovery after shutdown or reboot

1. Read the local handoff and current user pause decisions. Check shared mounts
   with a small create/write/flush/fsync/remove probe. Network-filesystem `df`
   free space can disagree with the account quota; test real writes.
2. Run the read-only inventory on both head and CPU worker. Check exact command
   identities and live children; a failed observation is unknown, not proof of
   a stopped task. Keep late successful deliveries and their reuse registrations.
3. Start only the approved service subset using
   `python -m tools.watch_benchmark_services --spec "$SERVICE_SPEC"`. A broad old
   specification may contain scoring services or retired routes. Keep one owner
   per service, ledger and matrix. A detached process survives shell exit, not
   a reboot; it needs a host process manager or an explicit restart.
4. Verify `/ready` **and a durable log write** from the relevant host. `/health`
   alone is listener liveness. Queueing/rate limiting remains a provider wait;
   storage failures, broken forwarding and CLI configuration failures are infra.
5. Resume the registered continuation controller with the frozen interpreter.
   Use `tools.watch_failed_retries` for authorized recovery and
   `tools.manage_matrix` for restartable dispatch. Preserve paused models,
   completed cohorts and existing in-flight cases.
6. Verify an actual response, tool execution and a valid delivery. Then verify
   that pending cases continue to be dispatched. Launching a process is not
   evidence that a video was generated.

After harness edits, run `pytest -q`, the pinned `pixelworld doctor` with
`--require-sandbox`, and a one-case generation smoke before a matrix. See
[retry policy](RETRY_POLICY.md), [provider recovery](PROVIDER_FAILURE_RECOVERY.md)
and [isolation](FILESYSTEM_ISOLATION.md).

## 5. Physical evaluation, FVD and subjective quality

Keep all three measurements separate. Scoring consumes accepted outputs and
never sends scores or reference outcomes to a generator.

| Evaluation | Entry point | Scope and evidence |
|---|---|---|
| Physics-IQ | `pixelworld physics-iq eval` / `tools.watch_physics_evaluation` | Verify CLI help and pinned full 198-case input join |
| RBench | `tools.evaluate_rbench_common_gpt61` | Official six-frame selection and GPT-branch aggregation; GPT-6.1-Sol |
| PhyGenBench | `tools.evaluate_phygen_three_stage_gpt55` with `--judge gpt61-sol` | Current three-stage MCQ protocol; per-judge output directory |
| PCVE | `tools.evaluate_pcve_full` / `tools.watch_pcve_gpu_evaluation` | Full 129-case tracking and metrics, official per-case PES |
| Appearance | `tools.evaluate_video_photographic_gpt61` | One GPT-6.1-Sol rubric, same frames and both presentation orders |
| Physics-IQ FVD | `tools.evaluate_physics_iq_fvd` | Generated distribution versus official reference distribution |

Check `python -m tools.MODULE --help` before invoking a scorer. The exact CLI
module and local evaluator/checkpoint directories are recorded in the local
handoff; some adapters retain an older filename while selecting a newer judge.

FVD is a **distribution distance**, not a pairwise reconstruction error.
Use the same extractor/checkpoint hash, frame sampling and reference pool for
all compared models within a benchmark: Physics-IQ official references; PCVE
source/reference videos; PhyGenBench frozen WISA-2048; RBench the approved
RoVid-X RGB pool when available. Lower is better. Distances from different
reference pools or extractor versions are not a common cross-benchmark scale.

PCVE's source videos can themselves look simulated and the requested edit may
be counterintuitive. Judge edited and source clips with the same appearance
protocol, then report the matched change where requested. The quality judge
does not substitute for physical-edit correctness.

Full scores require the full accepted roster and current source-manifest/video
hashes. Partial subsets, missing clips and stale score joins stay explicit.
For GPU work, use only verified idle devices; keep environment, caches, workdir
and outputs on shared storage. See [evaluation details](AUTOMATIC_EVALUATION.md).

## 6. Where videos and original records live

Use registered configs and manifests rather than directory names to select
results. Main generation is normally under `$BUNDLE/formal_generations/`, with
current native and recovered cohorts registered separately. Each accepted run
has `run_manifest.json`, `workspace/generate.py`, `workspace/pixels.npz`, frame
artifacts, exact input snapshot, native/tool logs and candidate history.

Seedance and MiniMax videos are external baseline artifacts. The baseline
video manifest records their exact paths, case IDs, hashes and media contracts.
MiniMax's local official inference, prompt-engineered inference, earlier
calibration outputs and published API scores are different provenance. A
complete prompt list does not establish complete generated videos.

| Storage channel | What belongs there |
|---|---|
| GitHub | Code, portable configs, protocol and public documentation |
| Private shared archive | Raw API inputs/outputs, tokens, full traces, code, candidates, exact configs, scoring originals and receipts |
| HF dataset | Sanitized previews, complete exported traces, code, indices, scores and upload receipts |
| HF static Space | Main and analysis views consuming the HF dataset |

The numbered private `benchmark/model/run_####` archive has per-member hashes
and readback receipts. A run archive with zero raw API pairs is not a complete
wire backup. Preserve the separately indexed original API archive and run-call
mapping. Do not infer hidden or missing reasoning from token counts.

## 7. Publish current results

The current targets are [SiYuanJasperMa/PixelWorld](https://github.com/SiYuanJasperMa/PixelWorld),
[HuggingFriends/PixelWorld](https://huggingface.co/datasets/HuggingFriends/PixelWorld)
and the [HF dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html).
Keep credentials and private paths out of Git and public traces.

Publication order is exporter → case/media/trace uploader → dataset progress →
main-suite dataset/Space publisher → analysis progress and cost-effect plots.
Use the supplied live-source registries and existing ledgers. A local page or
successful HF upload does not establish that a public URL is fresh: fetch the
public JSON and HTML, compare timestamps/commits, and test a case/video link.
Open tabs refresh HF every 60 seconds. See [publishing commands](HF_PUBLISHING.md).

A publisher contract mismatch is corrected by registering the exact approved
continuation and provenance. Do not weaken model, prompt, scope or media checks.
Public tables use the source of each score explicitly; published paper values
and local re-evaluations remain labeled.

### Local API routing and launch ownership

When an API bridge uses a loopback URL, keep that traffic out of an external
HTTP proxy. Export both forms before starting or recovering a controller:

```bash
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"
```

A `403 Forbidden` HTML response for a loopback `/v1/responses` URL can come
from the external proxy before the request reaches the model bridge. Compare
direct `/ready` with the same request under the controller's actual proxy
environment. Preserve the rejected attempt's events, repair routing and
classify a proven pre-model rejection as infrastructure failure.

The dispatcher records a launch PID before the child creates its artifact
directory. That PID remains a case owner during preparation and finalization;
an ended retry window must not start another attempt while it is alive.
Attempt directories are claimed atomically, so concurrent launches cannot
overwrite an existing run's native records or RGB artifacts.

## 8. Cleanup and handoff

Inventory the registered data first. Distinguish original records, independent
copies, hardlinks and reproducible temporary artifacts. Deleting a hardlink name
need not reclaim data. Keep current paper raw I/O, source references, active
files and the owner's retained subsets. A directory called `smoke` can contain
formally reused runs.

Delete only the reviewed authorized batch, retain its metadata, record exact
paths and pre-delete allocation, confirm absence and test real writes afterward.
The local handoff records completed deletion receipts and further candidates.
Do not recursively relocate an active run: recorded workspace aliases and raw
events must retain their original execution meaning.

At handoff, report main generation, extra retries, analysis generation and
evaluation separately. Include verified process state, latest successful
delivery, missing cases, raw-record coverage and public freshness. Record a
concrete next action; keep unsupported results and unverified counts explicit.

## Code map

| Area | Implementation |
|---|---|
| Current read-only operator entry | `tools/ops/status.py` |
| Config and media contracts | `pixelworld/config.py`, `media.py`, `constants.py` |
| Agent execution and budget | `runner.py`, `interaction.py`, `token_budget.py`, `usage.py` |
| Isolation and acceptance | `permissions.py`, `native_sandbox.py`, `audit.py`, `submission.py` |
| Dispatch and recovery | `supervisor.py`, `retries.py`, `scheduling.py`; `tools/manage_matrix.py`, `watch_failed_retries.py` |
| Trace and private storage | `recording.py`, `full_trace.py`, `proxy_trace.py`, `run_archive.py` |
| API transport | `traex_responses_proxy.py`, `provider_completion.py`, `log_storage.py` |
| Evaluation and publication | Stable `tools/` modules indexed in [tools/README.md](../tools/README.md) |

Historical executable module paths remain stable for old configs and receipts.
Discarded plans live in `docs/archive/`; private runtime scripts and artifacts
live under ignored `runs/`. Do not read dated launch plans as current dispatch
authorization.
