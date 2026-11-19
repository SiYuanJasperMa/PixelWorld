# Automatic evaluation of completed cohorts

Generation and evaluation use separate directories. Evaluation never feeds
scores, masks, reference outcomes, or judge feedback back into a generation run.
Existing validated deliveries and completed scores are reused.

## Monitoring generation and API activity

Video delivery times and API response times measure different events. The model
row shows the latest successful API request across that model's experiments;
each benchmark cell retains its own latest video delivery time. API activity
does not establish that a particular case is generating frames or has finished.

The live exporter reads the proxy `--log-dir` entries in the bundle's cloud
service registry, `state/recovered_worker_services.json`. It reads only a bounded
tail of each `requests.jsonl` index. HTTP 200 streams with errors or client
disconnects are not counted as successful responses; missing indexes remain
unknown. This monitoring never restarts generation because of a long wait.

The matrix manager separately detects a stuck dispatcher: its status must be
unchanged for at least 15 minutes, its command must match the owned matrix,
the kernel must report disk sleep (`D`), and it must have no active child
processes. On Linux with pidfd support, it pins that dispatcher process before
sending SIGTERM and lets the existing manager restart it. Queued model requests,
active generation children and unknown observations never trigger this recovery.
`--dispatcher-stale-seconds 0` disables it. Historical outputs and attempt
limits are preserved.

The publication service rebuilds the four cost-quality charts in a separate
background job when a completed main score or its recorded usage coverage
changes. A full benchmark point requires cost counters for the same complete
case count. Missing cache counters remain unknown, and partial cost pools cannot
price a full score. Token prices are explicitly labeled reference assumptions;
70/80% cache scenarios remain separate from reported cache usage.

PhyGenBench's chart uses the PixelWorld GPT-5.5 scale. Baselines evaluated under
a different judge stay in the main table as labeled references and are excluded
from that chart's shared score axis.

## PCVE

`tools.watch_pcve_gpu_evaluation` watches both `pcve_full.json` and
`pcve_text_analysis.json` under a prepared experiment bundle. It requires all
129 deliveries before scheduling, recognizes already running legacy evaluators,
and validates completed scores against the pinned evaluator and dataset.
It probes the explicitly supplied SSH workers before launching. An observation
failure is reported as unknown; it does not trigger a duplicate evaluation.

```bash
python -m tools.watch_pcve_gpu_evaluation \
  --bundle "$BUNDLE" --worker "$GPU_WORKER_1" --worker "$GPU_WORKER_2" \
  --python "$PCVE_EVAL_PYTHON" --ffmpeg "$PIXELWORLD_FFMPEG"
```

Each remote job runs from the shared repository and uses cloud directories for
all cache, temporary files, predictions, logs, and state. A cohort lock prevents
duplicate launches. GPU allocation requires at least 32 GiB free memory and
at most 20% observed compute utilization, and holds a per-host, per-GPU claim.
Unknown utilization does not count as an idle GPU. Other workloads are never stopped. Verified
shared GroundingDINO and CLIP weights must be prepared before scoring.

The dispatcher writes `state/pcve_gpu_dispatch.json`. Per-cohort logs and scores
are under `evaluation/pcve_standard720p24fps_scheduler` for the main experiment
and `evaluation/pcve_text_scheduler` for Text analysis.

### Table 1 scope and metrics

The primary PCVE score uses all 129 official cases: 92 SET, 7 ADD and 30 DELETE.
DELETE remains part of Table 1; the separate Table 2 removal analysis is not
reported. The main experiment table shows only PES. The PCVE detail page shows
all eight official metrics:

| Metric | Direction | Official implementation |
|---|---|---|
| PES | Higher | `eval/metrics/physics.py`, per-case floor and mean in `compute_metrics.py` |
| TE, pixels | Lower | `eval/metrics/physics.py` |
| Mask IoU | Higher | `eval/metrics/physics.py` |
| PSNR | Higher | `eval/metrics/perceptual.py` |
| SSIM | Higher | `eval/metrics/perceptual.py` |
| LPIPS | Lower | `eval/metrics/perceptual.py` |
| CLIP similarity | Higher | `eval/metrics/perceptual.py` |
| FVD | Lower | `eval/metrics/fvd.py`, I3D features |

PES is dimensionless, not a percentage. For each case, the official scorer
computes `max(1 - sum(TE_pred) / sum(TE_null), -1)` and averages the resulting
case scores. It does not take the ratio of two benchmark-wide TE means.
Official eligibility and missing-track handling are preserved.

The evaluator source and dataset are pinned by `pixelworld.pcve`; the manifest
hash is verified. Checkpoint paths and the shared tracking-cache directory are
portable overrides. No metric formula, tracker threshold or reference video is
changed. Paper Table 1 values are labeled published references; matching code
does not by itself establish identical tracker outputs to the authors' run.

### SAM2 native postprocessing

`tools.build_sam2_native_eval` builds the unmodified connected-component CUDA
kernel in cloud storage. `state/sam2_native_eval/ready.json` records the extension
hash, compiler archives, Torch/CUDA versions, supported CUDA architectures,
and 32 random-mask parity tests. The binary contains SM80 and SM90/PTX code for
the provided SM80/A800 and Hopper workers. Each evaluator executes a kernel on its
actual GPU before scoring, and rejects SAM2 postprocessing-fallback warnings.
The automatic worker requires this verified binary and loads it explicitly;
otherwise evaluation fails before scoring. Run from a provided GPU worker with
the shared evaluator Python environment.

Native-enabled full results are saved in each cohort's `sam2_native_v2/`
subdirectory. Existing evaluation results and accepted generation artifacts are
preserved. The live main score only uses the native-enabled result once all 129
cases and all eight finite aggregate metrics validate. Evaluation progress is
written per case to `execution.json` and published in the dashboard.
Existing video exports are reused only when both the accepted attempt's
manifest hash and the exported video's hash match. A changed export is rebuilt
in the new evaluation directory without overwriting the original.
Published reference values are versioned in `docs/PCVE_TABLE1_REFERENCE.json`.

GT/source tracking caches are isolated by dataset revision and extension hash
under `state/pcve_native_gt_tracks/`; they cannot reuse earlier tracking without
the native postprocessing. Source-copy and edited-oracle controls are run with
`tools.evaluate_pcve_controls`. The source-copy PES should be zero; edited-oracle
tracking can fall below one because the official tracker can miss objects.
Controls are diagnostics and never substitute for coding-agent predictions.

## PhyGenBench Text and Vision

The same GPT-5.5 adapter supports either track. Run a separate watcher and output
directory for each track:

```bash
python -m tools.watch_phygen_gpt55_evaluation \
  --config "$BUNDLE/configs/phygenbench_text_analysis.json" \
  --track text --output "$BUNDLE/evaluation/phygenbench_text_gpt55_judge"
```

The default track remains `vision-agent` for existing main experiments. Scores
remain explicitly labeled as the PixelWorld GPT-5.5 adaptation rather than the
official PhyGenEval. Live evaluation metadata keeps each analysis condition and
track in its own score location; a Vision score cannot fill a Text cell.

## Physics-IQ analysis evaluation

Use one watcher and a separate output directory per condition. Each watcher
waits for all 198 accepted deliveries of a model/track before evaluating that
cohort. Main Vision results remain the shared reference; they are not generated
or scored again for each analysis.

| Condition | Bundle config | Evaluation output directory | Additional argument |
|---|---|---|---|
| Text | `physics_iq_text_analysis.json` | `physics_iq_text_scheduler` | None |
| 480p | `physics_iq_res480_analysis.json` | `physics_iq_res480_scheduler` | None |
| 1080p | `physics_iq_res1080_analysis.json` | `physics_iq_res1080_scheduler` | None |
| 12 FPS | `physics_iq_fps12_analysis.json` | `physics_iq_fps12_scheduler` | `--evaluation-fps 24` |
| 48 FPS | `physics_iq_fps48_analysis.json` | `physics_iq_fps48_scheduler` | `--evaluation-fps 24` |
| Lower requested effort | `physics_iq_thinking_low_analysis.json` | `physics_iq_thinking_low_scheduler` | None |
| Higher requested effort | `physics_iq_thinking_higher_analysis.json` | `physics_iq_thinking_higher_scheduler` | None |

The watcher checks the pinned evaluator, dataset and Python dependencies before
entering its scheduling loop. Its Python interpreter must import `numpy`,
`cv2`, `pandas`, `scipy` and `matplotlib`. If dependencies are supplied through
`PYTHONPATH`, preserve that environment for both the watcher and its evaluator
subprocesses. Reuse the verified cloud environment instead of installing
packages separately for every watcher.

A launcher PID alone does not prove readiness. Verify the process command,
the matching output directory's `scheduler.json` updates, and the current log.
Report preflight failures separately from model generation failures. A failed
observation of a healthy evaluator does not authorize a duplicate launch.

### Common FPS evaluation grid

The frozen analysis uses a common 24 FPS evaluation grid. Pass
`--evaluation-fps 24` to `tools.watch_physics_evaluation` for the 12/48 FPS
conditions. At each evaluation timestamp, select the existing source frame
displayed at that time: repeat each 12 FPS frame twice and select every second
48 FPS frame. No RGB interpolation or generated intermediate frames are added.

The export records the raw media contract, source-frame indices, evaluated media
contract, and source/video hashes. Lossless decode is checked against those exact
selected RGB frames. The raw 60/120/240-frame generation artifacts and their
token counts remain unchanged. Official 24 FPS reference videos, masks, scoring
code, and dataset pins remain unchanged. Do not describe this as scoring against
native 48 FPS reference footage.
