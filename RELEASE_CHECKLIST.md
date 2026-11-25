# Release checklist

Current release target: **four-benchmark main suite** (Physics-IQ Verified,
RBench Common Manipulation, PhyGenBench T2V, PCVE-RigidBench), 720p / 24 FPS,
Codex 0.169.0, `case-only-v1`, 1M output tokens per real attempt, at most five
real attempts per case with infrastructure failures exempt; RBench's original
allowance is one, and owner-authorized sparse-tail recovery is recorded separately.

## Code

- [ ] `pytest -q` passes in a fresh Python 3.11 environment.
- [ ] `pixelworld doctor --config ...` is green with Codex 0.169.0.
- [ ] Matrix supervisors run on the packaged runtime Python; a new attempt's
  `permissions_profile.json` contains the runtime `bin/lib` roots and no
  `bwrap: execvp .../codex` error appears.
- [ ] A fresh case passes every enabled Track before a full matrix.
- [ ] No credentials, local absolute paths, generated runs, references, masks,
  or scores are tracked.
- [ ] Physics-IQ evaluator commit, dataset revision, prompt hash, FPS, duration
  and resolution are unchanged, or the protocol version is bumped.
- [ ] Public examples contain environment-variable names only.
- [ ] Generated outputs and official data remain in ignored directories.

## Protocol and evaluation

- [ ] RBench uses one generated candidate per official sample; no quality
  rerolls. Infrastructure-invalidated attempts carry an explicit
  `infra_reclassification` record and do not consume a model attempt.
- [ ] PhyGenBench uses the frozen three-stage MCQ adaptation with GPT-6.1-Sol.
- [ ] Appearance score, physical score and FVD remain separate columns; the
  appearance score is never reported as a physics score.
- [ ] A partial cohort never displays a mean; full-scope scores require every
  case and every judge call.
- [ ] Private verified archives exist for every paper run, with readback
  receipts and an intact paper-run index.

## Publication

- [ ] `tools.live_benchmark_data --local-only` keeps
  `publication/pixelworld/catalogue.json` fresh.
- [ ] `tools.publish_hf_incremental` has uploaded every closed case's detail,
  preview and trace; its ledger is current.
- [ ] `tools.watch_hf_dataset_progress` mirrors `live/` progress to
  `HuggingFriends/PixelWorld`.
- [ ] `tools.publish_main_suite_hf` has published the Space snapshot; the page
  polls the dataset `metadata/main_suite_status.json` every 60 seconds and
  replaces its main content with the matching `dashboard/index.html` snapshot.
- [ ] The dataset README, `metadata/protocol.json` and
  `metadata/main_suite_status.json` match the current protocol and snapshot.
- [ ] The visual site is tested for playback and Trace links.
- [ ] `docs/HF_PUBLISHING.md` and `docs/STORAGE_LAYOUT.md` match the deployed
  commands and directories.
