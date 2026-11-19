# PhyGenBench A/C/D initialization diagnostic

Status: **shelved by user; historical evidence only**. No new runs or expansion.

This analysis investigates whether scene appearance limits coding-agent T2V.
It does not tune a Qwen-assisted method to maximize benchmark scores.

| Route | Initialization | Video generation |
|---|---|---|
| A | None | Direct coding-agent T2V; compatible main deliveries reused |
| C | Coding-agent pixel still, then Qwen image-conditioned photographic restyling | Coding-agent I2V |
| D | Qwen text-to-image pre-action still | Coding-agent I2V |

B is excluded. Qwen never generates subsequent video frames. The initialization
preparer runs outside the agent sandbox; video agents see only their case inputs
and the ordinary PixelWorld I2V contract. This is a separately versioned analysis,
not a change to the four main generation benchmarks.

The first smoke uses GPT-6.1-Sol, Vision, requested High, 1280×720, 24 FPS,
120 frames (five seconds), native Codex 0.169.0 and case-only-v1 permissions.
The ten official captions are frozen with random seed 20261119 and stratified
3/3/2/2 across Mechanics, Optics, Thermal and Material. Selection precedes new
initialization and evaluation. IDs and caption hashes live in the generated
manifest; prompt text is unchanged for A/C/D video generation.

C's standalone still task describes the same event as context and requests its
pre-action state. Qwen-Image-2.1 uses a pinned checkpoint, 40 steps, CFG 5,
native 1536×864 and seeds 0, 1, 2 for each route/case. The native image is resized
uniformly with Lanczos to 1280×720. Seeds are reviewed independently for caption
and pre-action suitability, without video, downstream scores or physical-law
annotations. Select the first acceptable seed in ascending order. If none passes,
retain seed 0 and explicitly flag the unsuitable initialization. This fallback is
fixed in advance and is not a positive result. Preserve all candidates and reviews.

The review judge is GPT-5.5. Video evaluation uses the same PixelWorld GPT-5.5
protocol as the main PhyGen comparison: eight chronological frames, maximum
judge-image edge 768, physical consistency from 1 to 5. This is not the official
GPT-4o PhyGenEval. Report per-route delivery and suitability coverage alongside
paired video scores, rather than silently removing bad initializations.

The current frame-sampling revision is `chronological-rgb-v2`. Read frames by
their numeric index (`00.png` through `119.png`); filename lexicographic order
places `100.png` before `11.png` and is invalid. The corrected preview is
`video_chrono_v2.mp4`, encoded with a numeric FFmpeg sequence. Earlier previews
and `evaluation/` scores are retained as legacy sampling outputs. Corrected
smoke scores live in `evaluation_chronological_v2/`; only this revision is used
for current comparisons. No agent code or generated RGB is changed by this fix.

A reuses its first accepted main delivery. Record its original budget, prompt,
runtime and permission lineage. Earlier main deliveries may have a 100k budget,
while new smoke attempts allow 1M output tokens. Reuse therefore provides a
diagnostic comparison; it alone does not establish a strictly budget-matched
causal effect. Costs for C include the pixel-still stage and Qwen restyling;
D includes Qwen T2I. Report both video-stage cost and total initialization cost.

New real generations use at most five genuine attempts. Infrastructure errors
do not consume the genuine-attempt budget. No retries are driven by low quality
scores. Initial-image preparation, coding-agent I2V, evaluation and publication
have separate progress states. Videos, candidate images, traces, scores and
runtime paths stay in generated cloud-storage directories and the HF dataset.

Preparation:

```bash
python -m tools.prepare_phygen_acd_smoke --release "$PIXELWORLD_RELEASE"
```

Run `tools.qwen_phygen_acd_batch` on a free GPU worker after changing back to
the cloud repository. Run `tools.watch_phygen_acd_smoke` with the release root
and a credential-file argument on the monitoring host. Register the pixel-still
and I2V matrices under the existing shared scheduling root and watchdog; do not
launch an independent unrestricted matrix. The coordinator exposes ready inputs
incrementally and registers their existing matrix managers. A dedicated retry
watcher uses the same five-attempt accounting.

The public page is `phygen_acd_smoke.html` in the PixelWorld HF dashboard.
Its generated progress and playable assets live under `live/smoke/phygen_acd10/`
in the HF dataset. A delivered video appears separately from its uploaded preview;
links are published only with the corresponding assets. Use the worker's fresh
status for running remote cases; local PID lookup cannot establish remote liveness.
