# Main-suite update: Robotics subset, paired harnesses, and video quality

The new main table is a four-benchmark suite. Historical RoboJudge runs and
scores remain available; they are not a column in this protocol version.

| Benchmark | Task | Main score and provenance |
|---|---|---|
| Physics-IQ | Image-to-video | Existing pinned Verified evaluator and fixed 24 FPS evaluation grid |
| RBench Common Manipulation | Image-to-video | Pinned upstream Common Manipulation GPT branch with GPT-6.1-Sol; not the combined GPT + Qwen leaderboard score |
| PhyGenBench | Text-to-video | Existing three-stage ordinal MCQ adaptation with GPT-6.1-Sol; older GPT-5.5 records stay separate |
| PCVE-RigidBench | Video editing | Existing native 129-case evaluator and PES; do not substitute a reduced subset |

Only compatible existing deliveries are reused. For PhyGenBench re-scoring,
use a new output directory per judge version. A judge change does not require
regenerating videos. Scope or provenance mismatches block a full-scope score.

## GPT-6.2-Sol

Use the exact route identifier from the authorized provider, not a family
alias. For the TRAEX route it is `traex/GPT-6.2-Sol`. Configure a local Responses
base URL and supply the credential through `PIXELWORLD_API_KEY`; never commit
a key or a private endpoint. Check a vision/tool round trip and the native
one-case RGB delivery before starting the configured main and analysis cohorts.
For the isolated Codex run use the frozen **0.169.0** executable staged on a
volume visible to the sandbox. A launcher whose target binary is under a
hidden user home can answer `--version` and still fail on agent tool commands.

## RBench preparation and scoring

The official [RBench dataset](https://huggingface.co/datasets/DAGroup-PKU/RBench)
is pinned to `6bdccf349ff5a8f68302428351e94f34ecd62450`. Stage the 50 JPEG files in
`imgs/common_manipulation/` and `prompts/common_manipulation_prompts.json` with
a download manifest (`dataset`, `revision`, `subset`, and `files: [{relative,
sha256}]`). Do not expose the judge annotations to the agent:

```bash
python -m tools.stage_rbench_common --source "$RBENCH_STAGED_SOURCE" --output "$RBENCH_AGENT_INPUTS"
```

The generation contract is I2V, 720-pixel short edge with native aspect ratio,
120 frames at 24 FPS. There is **one generated candidate per official sample**;
no Best-of-N or quality rerolls. The original JPEG is decoded losslessly into a
PNG input; the output conditioning resize is explicitly versioned.

Use upstream [ReVidgen](https://github.com/DAGroup-PKU/ReVidgen) commit
`b03df27f0376faa148dcd8cd620a1989a32ca979` and its dependencies. Export approved
outputs as the exact numeric names `0001.mp4` through `0050.mp4`. The scoring
adapter retains the original six-frame grid (sample 16 frames, then keep six),
prompts, JSON extraction, and score formula. It changes the VLM and transport
only. The upstream Chat Completions seed is not available through the Responses
transport and is explicitly marked unsupported, not simulated.

```bash
python -m tools.evaluate_rbench_common_gpt61 --official-repo "$REVIDGEN" \
  --videos "$RBENCH_VIDEOS" --prompts "$RBENCH_PROMPTS" \
  --output "$RBENCH_SCORES" --base-url "$JUDGE_RESPONSES_BASE_URL"
```

`PIXELWORLD_JUDGE_API_KEY` is needed for RBench and the PhyGen opt-in
`--judge gpt61-sol --responses-base-url ...`. A one-case acceptance run with
`--acceptance-only` never claims a complete subset score. For a running RBench
matrix, `python -m tools.watch_rbench_common_scores --help` documents the
incremental scorer: it looks up accepted single candidates, reuses a matching
pilot score, never asks the generator for another attempt, and withholds the
50-case mean while generation or judge cases are missing.

## Harness and quality analysis

See [the paired harness ablation](HARNESS_COMPARISON.md): Codex baseline,
OpenCode and proprietary Claude Code on the same Physics-IQ Vision cases. An
adapter must pass isolation, model, visual-input, video and archive acceptance
before matrix dispatch. Pi pilot evidence is historical.

Run a separate **GPT-6.1-Sol subjective video-quality** comparison across all
four entries, with one common quality rubric. Do not report this score as
physical QA. Retain PhyGenBench FVD and add benchmark-matched Physics-IQ and
PCVE FVD. The RBench validation release provides first frames and prompts, not
a matched reference video pool; select and document a suitable real reference
set before reporting RBench FVD. Reference-set FVD values are not comparable
across tasks. The hypothesized T2V/I2V/V2V quality ordering is not a result.

**RBench reference check.** The pinned public RBench tree contains zero video
files; Common Manipulation provides 50 stills and prompts. The same authors'
CC BY 4.0 [RoVid-X release](https://huggingface.co/datasets/DAGroup-PKU/RoVid-X/tree/32faf783ba532e1107881b29c612be0aec16b888)
is a candidate reference distribution, not a source of identified ground-truth
continuations. Its preview has only 36 RGB clips (the other 72 video files
are depth and optical flow). Do not substitute those previews or an all-arm
robotics dataset for the 50-case mix: 28 prompts name humanoids, and the
viewpoints are 31 third-person (including one misspelling) and 19 first-person.
Before reporting an exploratory RBench FVD, freeze an independent RGB pool
stratified by robot, viewpoint and action family, exclude duplicate or
near-duplicate source sequences, and use the same case roster, sampling and
reference IDs for every model. Leave FVD unavailable until that join or pool
has been validated; this does not block RBench generation or physical QA.

The photographic runner uses the original anchored 1–5 appearance rubric,
eight video frames, anonymous labels, and both presentation orders. Its
manifest pairs by benchmark and case ID; missing clips are not imputed. For
I3D FVD, the Physics-IQ runner checks the official 198-way take-1 file join,
uses sixteen frames from each submitted/reference video, and withholds results
for incomplete model cohorts. A `--smoke` result is never a full score. Reuse
PCVE FVD only from the matching pinned official 129-case metrics; do not rerun
an identical evaluation simply to copy that score into a quality panel.

```bash
python -m tools.evaluate_video_photographic_gpt61 --manifest "$QUALITY_INPUT_MANIFEST" \
  --output "$PHOTO_OUTPUT" --base-url "$JUDGE_RESPONSES_BASE_URL"
python -m tools.evaluate_physics_iq_fvd --manifest "$QUALITY_INPUT_MANIFEST" \
  --references "$PHYSIQ_OFFICIAL_24FPS_TESTING_DIR" --i3d "$I3D_CHECKPOINT" \
  --output "$PHYSIQ_FVD_OUTPUT" --device cuda
```

Each manifest video needs `benchmark`, `case`, `model`, `source`, `identity`,
`frames`, and `fps`. The source must identify the accepted run or a matched
video comparator. Preserve the manifest, score version, and reference scope
with any published result.

## Private verified archives

Opt in with `PIXELWORLD_VERIFIED_ARCHIVE_ROOT` pointing at durable private
storage separate from the working run. `PIXELWORLD_ARCHIVE_BENCHMARK` can mark a
stable benchmark/condition name; `PIXELWORLD_PROXY_RECORD_ROOT` points to the
explicit authorized request-log directory. Each closed attempt receives a
numbered `benchmark/model/run_####` folder, original source identity, archive
with per-member SHA256, and an independent readback receipt. Missing raw wire
roles are reported, not replaced by token counts. After successful readback,
new verified archives may remove `pixels.packing.npy` or `pixels.npz.partial`
only if byte-identical to the corresponding archived tensor content, the
submission validated, and every known request has an exact input body and a
response/event body. Interrupted drafts, final outputs, traces, and proxy logs
are not pruned. Set `PIXELWORLD_ARCHIVE_PRUNE_REPRODUCIBLE=0` to disable this
cleanup. Do not publish private archives or raw requests with the code.

`python -m tools.archive_rbench_scores --scores "$RBENCH_SCORES" --archive
"$PIXELWORLD_VERIFIED_ARCHIVE_ROOT" --report "$PRIVATE_ATTACHMENT_REPORT"`
adds a readback-verified score under its numbered run only when the exact
source manifest hash and run identity agree. It does not infer a join to
unidentified judge API calls.
