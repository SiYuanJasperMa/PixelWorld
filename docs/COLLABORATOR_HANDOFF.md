# Collaborator handoff: one additional main-table model

Use this text when assigning Kimi, GLM, Qwen or another exact model version to
a collaborator. Fill in the bracketed fields; do not broaden the task.

```text
Run one additional PixelWorld main-table model with the frozen public harness.

Repository: https://github.com/SiyuanMaCS/PixelWorld
Required commit: 2f6844943079d69dd4fb8c66b9c7416c0845ecc4 or a later owner-approved commit
Required Codex CLI: 0.169.0
Exact model/API ID: [FILL IN; do not use only a family name]
Provider route: [builtin or Responses-compatible endpoint]
API credential environment variable: [FILL IN NAME ONLY; never send the value]
Frozen input-config root or artifact URI: [OWNER PROVIDES OUT OF BAND]

Main track: vision-agent (paper name: Vision) only.
Benchmarks and output contracts:
- Physics-IQ Verified: 198 cases, 1280x720, 24 FPS, 120 frames / 5 s.
- RoboJudge shared-50: 50 cases, preserve aspect ratio with 720-pixel short
  edge, 24 FPS, 120 frames / 5 s.
- PCVE-RigidBench: 129 edits, official source aspect/timeline; current set is
  1280x720, 24 FPS, 96 frames / 4 s.
- PhyGenBench official T2V: 160 prompts, direct pixel-space photographic target,
  1280x720, 24 FPS, 120 frames / 5 s.

Keep the frozen prompts/rules, case-only-v1 filesystem policy, requested High
effort, 100,000 reported output-token budget per real attempt, and at most five
real attempts per case. Infrastructure/key/rate-limit/transport failures are
recorded separately and do not consume model attempts. Do not add a learned
renderer, image/video generator, physics engine, subagent, reference video,
mask or evaluator feedback to generation.

1. Copy configs/collaborator-model.example.json to an ignored *.local.json
   roster and set only the exact model/provider fields.
2. Run pixelworld doctor and one real Vision smoke for each of the four
   benchmark adapters.
3. Send the smoke bundle/status and one playable output per benchmark to the
   owner for review before full dispatch.
4. After the generation readiness gates are approved, launch the full matrix
   through tools/final_experiments.py. Preserve every attempt and raw usage.
5. Return: repo commit, local roster without secrets, bundle config/status,
   artifact-store URI, evaluation JSON when complete, and monitoring URL.

Generated videos, RGB tensors, benchmark data and credentials must not be
committed to GitHub. See docs/ADD_MODEL_RUNBOOK.md for commands and packaging.
```
