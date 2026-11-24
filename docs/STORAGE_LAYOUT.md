# Storage layout

Choose durable shared-storage roots before launching. Permanent paths must
resolve to shared storage, including symlink targets. The local operator map is
kept beside the private run archive; machine-specific paths are not part of this
repository.

| Root | Purpose | Public? |
|---|---|---|
| `$PIXELWORLD_REPO` | GitHub code checkout and portable config templates | Code only |
| `$PIXELWORLD_RUNTIME` | Frozen Python, Codex 0.169.0, ffmpeg and sandbox tools | Version/hash metadata |
| `$BUNDLE` | Frozen configs, generation attempts, publication staging and service state | Sanitized exports |
| `$NIGHTLY_ROOT` | Current GPT-6.2, RBench and harness cohorts, evaluations | Sanitized exports |
| `$PRIVATE_ARCHIVE_ROOT` | Full original input/output, native traces, numbered run index and readback receipts | Private |
| `$PROXY_RECORD_ROOT` | Exact API request/response bodies and in-flight markers | Private |

```text
$BUNDLE/
  configs/                 benchmark/model configs and limits
  formal_generations/      benchmark/model/track/case_ID_attempt_NN/
  publication/pixelworld/   sanitized catalogue, details and media
  state/                   upload/retry ledgers and service ownership
$NIGHTLY_ROOT/
  gpt62_cohorts/            main and analysis configs/runs
  rbench_full_dispatch/    one real candidate per sample; infra retries separate
  controlled_harness_matrix/
  evaluation/              objective scores, appearance scores and FVD
  verified_*/              verified private archives
$PRIVATE_ARCHIVE_ROOT/
  benchmark/model/run_NNNN/ original provenance, calls, usage and receipts
```

Each attempt keeps its input snapshot, task/rules, generator and candidates,
manifest, native events and token counters. `logs/` and archives are external to
the model-visible workspace. Keep all attempts used in the paper, including
failed and infrastructure-invalidated records. Retry directory ordinals never
change. See [RETRY_POLICY.md](RETRY_POLICY.md).

On our packaged runtime, launch matrices with `$PIXELWORLD_RUNTIME/bin/python`
and set `PIXELWORLD_CODEX` to the real, pinned executable under that runtime.
The filesystem profile derives library roots from the interpreter. A symlink to
a binary hidden from the sandbox can pass `--version` but fail when tools run.
Verify tool execution and isolation in the one-case smoke; `doctor` alone does
not certify the complete workflow.

The publication exporter owns `publication/pixelworld/`. The incremental HF
uploader owns its `hf_incremental.json` receipt ledger. The main-suite publisher
owns dataset `metadata/main_suite_status.json`, `metadata/experiment_index.json`
and `dashboard/index.html`, plus the Space homepage. Exactly one process owns
each ledger. Commands are in [HF_PUBLISHING.md](HF_PUBLISHING.md).

Cleanup uses archive readback receipts. Preserve original paper runs and active
proxy records. Reproducible upload packages can be removed after their HF
commit and local receipt are durable; tensor intermediates require the stronger
checks in [STORAGE_POLICY.md](STORAGE_POLICY.md). Never scan frame directories
just to inventory attempt manifests: use the registered model/track roots.

## Recovery backfill

For a frozen runner without an archive hook, use the bounded external worker.
A registry names exact configs and a unique archive scope per condition:

```bash
python -m tools.archive_recent_registered_runs \
  --registry "$ARCHIVE_CONFIG_REGISTRY" \
  --archive-root "$PRIVATE_ARCHIVE_ROOT" \
  --proxy-route "$RESPONSES_BASE_URL=$PROXY_RECORD_ROOT" \
  --report "$NIGHTLY_ROOT/state/archive_watch.json" \
  --limit 4 --apply --watch --poll-seconds 120
```

The worker verifies archives on readback and never prunes the source. Its
per-cycle limit deliberately prevents a full backlog census. `known_requests=0`
or zero matched raw pairs does not establish complete API coverage; preserve
the native trace and original logs and inspect the separate wire receipts.
Future runners with the archive hook can set `PIXELWORLD_VERIFIED_ARCHIVE_ROOT`,
`PIXELWORLD_PROXY_RECORD_ROOT`, and a unique `PIXELWORLD_ARCHIVE_BENCHMARK` in the
parent environment. Do not embed those private paths or credentials in public
config templates.
