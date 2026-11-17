# Configuration guide

## Current main protocol

The main experiment is **Vision only**, 720p / 24 FPS, Codex 0.169.0,
`case-only-v1`, and **1,000,000 reported output tokens per real attempt**.
Use these templates:

- [final-models.example.json](final-models.example.json) — full roster shape.
- [collaborator-model.example.json](collaborator-model.example.json) — one
  additional exact model version.
- [physics-iq-720p24fps.example.json](physics-iq-720p24fps.example.json) —
  Physics-IQ 720p adapter.
- [pcve-full.example.json](pcve-full.example.json) — PCVE-RigidBench full set.

The 1M budget is a 2026-11-17 amendment. Earlier completed deliveries retain
their recorded 100k budget; do not relabel them as 1M runs.

## Legacy examples

These remain for provenance and old experiment continuation. They use 100k and
must not be presented as the current main protocol:

- `benchmark.example.json` — minimal 256x144 harness example.
- `robojudge.baseline.example.json` — frozen native-100k RoboJudge baseline.
- `physics-iq-4k24fps.example.json`, `physics-iq-512w24fps.example.json` —
  resolution/FPS analysis.
- `pcve-smoke.example.json`, `openve-smoke.example.json`,
  `gpt55-responses.example.json` — historical smokes.

Never put API keys in a config. The `env_key` field names the environment
variable; the value stays in the parent shell.
