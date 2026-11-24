# Storage layout and ownership

All permanent artifacts live on shared BN storage. A `/mnt/bn` path must resolve
to BN storage; a symlink to a local disk is not acceptable for permanent data.
Generated media, traces and scores are never committed to the code repository.

## Code and frozen main experiment

```text
/mnt/bn/embodied-lf3/masiyuan/PixelWorld/
  pixelworld/                         harness code (tracked)
  tools/ tests/ docs/ configs/        code, tests, protocol docs (tracked)
  runs/bench_codex_20261109/
    inputs/                           prepared benchmark inputs (pinned)
    runtime_main_worker_20261116/     packaged Python + codex + bwrap + ffmpeg
    queue_proxy_logs*/                historical proxy wire logs
  runs/final_release_candidate_20261117/
    configs/                          frozen 7-model configs and limits
    code_snapshot_runtime_fix/        harness snapshot used by legacy cohorts
    formal_generations/{physics_iq,phygenbench,pcve,robojudge}/
    publication/pixelworld/           sanitized live bundle consumed by HF
    state/                            retry ledgers, HF incremental ledger, publisher state
  vendor/                             evaluator sources and checkpoints (ignored)
```

The `runtime_main_worker_20261116/bin/python` is the required interpreter for
matrix supervisors: the bwrap profile derives its read roots from
`sys.executable`, and the sandboxed agent must be able to exec the pinned Codex
binary from the runtime `bin/` directory. Running supervisors with the system
Python produces `bwrap: execvp .../codex: No such file or directory` and every
attempt fails before writing frames.

## Current four-benchmark suite

```text
/mnt/bn/embodied-lf3-data/masiyuan/pixelworld-nightly-20261124/
  gpt62_cohorts/                      GPT-6.2-Sol main cohorts + analysis cohorts
  rbench_full_dispatch/               RBench 8-model generation and roster queue
  controlled_harness_matrix/          Codex / OpenCode / Claude Code comparison
  acceptance/                         benchmark acceptance and canaries
  harness_acceptance/                 native harness executable locks and probes
  evaluation/                         Physics-IQ, PhyGenBench, PCVE, RBench, appearance, FVD
  verified_runs/ verified_*           private per-attempt archives with readback receipts
  main_suite_status_v2.json           dashboard snapshot
  main_suite_hf_publisher.json        Space publisher proof
  restart_20261124/                   current service launcher, logs and ledgers
```

## Proxy wire logs

`/mnt/bn/embodied-lf3/masiyuan/traex_api_proxy/proxy_logs*` holds request,
response and event bodies. `local.key` is a private loopback credential; never
print, commit or publish it. The paper-run full-text archive is
`/mnt/bn/embodied-lf3-data/masiyuan/pixelworld-formal-fulltext-20261123/` with
the run index at `/mnt/bn/embodied-lf3-data/masiyuan/pixelworld-paper-by-run-20261123/`.

## Ownership

| Path | Writer |
|---|---|
| `formal_generations/`, `gpt62_cohorts/runs/`, `rbench_full_dispatch/generations/` | matrix supervisors |
| `publication/pixelworld/` | `tools.live_benchmark_data --local-only` |
| `state/hf_incremental.json` | `tools.publish_hf_incremental` |
| `state/hf_progress_watcher.json` | `tools.watch_hf_dataset_progress` |
| `main_suite_hf_publisher.json` | `tools.publish_main_suite_hf` |
| `restart_20261124/launched.json` | `restart_20261124/launch.py` |

## Retention rules

- Never delete a successful submission, a run manifest, a token ledger, a trace
  archive, or an evaluator receipt.
- Prune only reproducible intermediates (`pixels.packing.npy`,
  `pixels.npz.partial`) after the verified archive readback proves byte
  identity. See `docs/STORAGE_POLICY.md`.
- Keep the frozen inputs under `runs/bench_codex_20261109/inputs/`; every run
  manifest hashes them.
- Move finished non-paper experiments out of the active nightly root only after
  their records are archived; do not renumber attempt directories.

## Verification

```bash
du -sh /mnt/bn/embodied-lf3-data/masiyuan/pixelworld-nightly-20261124 \
       /mnt/bn/embodied-lf3/masiyuan/PixelWorld/runs/final_release_candidate_20261117
find /mnt/bn/embodied-lf3-data/masiyuan/pixelworld-nightly-20261124 -name run_manifest.json | wc -l
```

The dashboard snapshot's `updated_utc` and the HF dataset
`metadata/main_suite_status.json` must agree after a publication cycle.
