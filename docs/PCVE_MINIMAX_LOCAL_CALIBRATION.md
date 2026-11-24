# Local MiniMax-H3 evaluator calibration

On 2026-11-19 the user authorized expansion to all129 official cases. The
completed ten-case calibration remains in `evaluation_subset/`; its videos are
reused. Generate the remaining119 with the same settings and use
`tools.watch_minimax_pcve --full129` to score all129 into `evaluation/`.
`subset.json` remains the original ten-case definition. Full129 includes the
official30 DELETE cases; no separate Table2 removal analysis is requested.
The full result is a local reproduction, with commercial API equivalence still
unverified. GPU group sizes may be four or eight without changing sampling.

To reuse the faster A800 worker after its original shard, the scoped
`tools.watch_minimax_pcve_backfill` waits for that shard and GPU launcher to
finish, then selects at most12 pending slow-shard cases from the tail. Active
and completed cases are excluded. A conservative time-separation check keeps
the main worker away from this bounded batch. Existing per-case locks and
completed-video hashes remain enforced. Separate backfill status preserves the
original shard record. This changes scheduling only; no prompts, model weights,
sampling settings or evaluation parameters are changed.

This run checks PCVE scoring on a video-generation baseline. It is a fixed
ten-case calibration subset, not a new full129 result. The subset is selected
before generation with seed42: seven SET, one ADD and two DELETE cases, covering
mass, friction, restitution and initial velocity across several scenes.
The same subset should be used when comparing against existing coding-agent
results; its mean cannot be treated as the paper's full-benchmark mean.

Generation uses the complete `MiniMaxAI/MiniMax-H3` checkpoint at revision
`42ed227ee7df40d41602854ae760620d6eb651fe`, including `transformer_ref`. The clean
FastVideo runtime is pinned at `d995516da00c24105aa841df1e690d3cd8a6c173`.
The model receives only the official source video and original quantitative
English instruction. No target video, trajectories, judge feedback or
downstream-motion description is supplied to generation.

Local settings: Ref2VA, 1344x768,24FPS,124 requested frames,50 sigma-grid
points, CFG1, seed42, BF16 DiT, and four GPUs with sequence parallelism.
The original native inference settings are retained. The paper's Table5 lists
107 output frames and96 evaluated frames for its768p commercial API baseline;
the official API adapter requests four seconds and does not expose a sampling
step count or submit its configured seed. Local and commercial API identity is
therefore not asserted. Approximate generation settings were explicitly
accepted for this evaluator-calibration experiment.

Preserve the raw local video. Evaluation exports only its first96 frames at
the existing24FPS, without interpolation or time scaling. The pinned original
PCVE evaluator normalizes prediction coordinates to the reference resolution.
All caches, runtime files, model weights, outputs and logs stay on cloud storage.
Use short temporary directories under the shared workspace's `.pwtmp/`:
Python/Torch IPC uses Unix-domain sockets, whose path length is limited.
An overlong TMPDIR caused the initial smoke to fail before video delivery; this
is recorded as an infrastructure failure rather than a model-quality failure.

`tools.watch_minimax_pcve` continues the smoke, reuses its valid first video,
dispatches the fixed subset through `tools.generate_minimax_pcve`, then invokes
`tools.evaluate_minimax_pcve`. `tools.run_gpu_group` holds the same per-GPU
claims as the evaluation workers and verifies idle GPUs; unrelated workloads
are not interrupted. Completed exports are verified and reused on restart.

The primary calibration uses released PCVE code at
`e262f93e9ab69208023413f342adf9d80fa098e5` and the same data and verified SAM2
native extension as the coding-agent evaluation. Report the subset scope and
configuration with every score. A score near the published MiniMax PES-0.096
supports agreement but is not sufficient proof of evaluator correctness.

Independent saved-track replay also checks the known prediction/null filtering
asymmetry. Diagnostic symmetric gating makes the source-copy control exactly
zero across129 cases. It changes coding-agent mean PES by less than0.005 in
paired replay, so this particular implementation discrepancy does not explain
the large apparent advantage alone. The reference-video oracle still has
tracking/identity errors, especially on removal cases. Diagnostic scores remain
separate from released-code results and are not silently substituted.
