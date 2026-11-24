# Collaborator handoff: one additional main-suite model

Fill in the owner-provided fields and send this assignment with the exact
release commit and frozen configs. Commands are in
[ADD_MODEL_RUNBOOK.md](ADD_MODEL_RUNBOOK.md).

```text
Run one additional exact-version PixelWorld model under the current main suite.

Repository: https://github.com/SiyuanMaCS/PixelWorld
Required release commit: [OWNER PROVIDES; record before running]
Required Codex CLI: 0.169.0 [record executable hash]
Exact model/API ID and stable model key: [FILL IN; no family aliases]
Provider route: [builtin or Responses-compatible endpoint]
API credential environment variable: [NAME ONLY; never send its value]
Four frozen benchmark configs and input manifest URI: [OWNER PROVIDES PRIVATELY]
Durable output/archive URI: [OWNER PROVIDES]

Main track: vision-agent (paper name: Vision).
- Physics-IQ Verified: 198 I2V cases, 1280x720, 24 FPS, 120 frames / 5 s.
- RBench Common Manipulation: 50 I2V cases, native aspect ratio with a
  720-pixel short edge, 24 FPS, 120 frames / 5 s. Exactly one real candidate
  per sample; no Best-of-N or quality rerolls. Infrastructure retries are exempt.
- PhyGenBench: 160 official T2V prompts, photographic pixel-space target,
  1280x720, 24 FPS, 120 frames / 5 s.
- PCVE-RigidBench: full 129-case V2V set, 1280x720, 24 FPS,
  96 frames / 4 s, preserving the official editing timeline.

Keep frozen prompts/input hashes, case-only-v1 isolation, requested high
reasoning effort and 1,000,000 reported output tokens per real attempt.
The general cap is five real attempts per case; RBench is limited to one.
Charge actual reported usage, including response overshoot. Quarantine recorded
infrastructure failures separately. Preserve every attempt and native record.

The agent directly writes RGB frames. Do not introduce image/video generators,
learned renderers, physics engines, subagents, reference continuations, masks
or evaluator feedback. Text and harness comparisons have separate analysis
configs. RoboJudge and Pi are historical and outside this assignment.

1. Make ignored local copies of the four supplied benchmark configs. Change
   only the new model/provider and designated run roots; preserve the protocol.
   configs/collaborator-model.example.json is a roster template, not a suite.
2. Run pixelworld doctor --require-sandbox with each config, then one real
   Vision smoke per benchmark. Verify image/tools, isolation, RGB delivery,
   native usage/budget accounting and the pinned evaluator smoke.
3. Return the four smoke bundles and admission evidence to the owner. Keep
   accepted smokes in the full-run roots or use an explicit verified reuse map.
   Never regenerate an accepted RBench smoke or reroll its real failure.
4. Once admission is recorded, run pixelworld matrix run --config CONFIG
   for each supplied benchmark config; monitor with pixelworld matrix status.
   tools/final_experiments.py still maps the older RoboJudge suite and is not
   the launcher for this four-benchmark protocol.
5. Evaluate only complete matching scopes. Return commit/version, sanitized
   configs and input hashes, status/usage/retry provenance, archive URI and
   receipts, complete evaluation provenance when available, and monitoring URL.
6. Follow docs/HF_PUBLISHING.md for public sanitized traces and media. Keep
   original raw API request/response archives private in durable LF3data storage.

GitHub receives code, templates and protocol/provenance notes. Credentials,
local private paths, benchmark inputs/references, generated videos/tensors,
scores and raw API archives must not be committed to the source repository.
```
