# Automatic evaluation of completed cohorts

Generation and evaluation use separate directories. Evaluation never feeds
scores, masks, reference outcomes, or judge feedback back into a generation run.
Existing validated deliveries and completed scores are reused.

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

## Physics-IQ FPS analysis

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
