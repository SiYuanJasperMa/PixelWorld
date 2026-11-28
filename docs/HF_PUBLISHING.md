# Hugging Face publishing

[HuggingFriends/PixelWorld](https://huggingface.co/datasets/HuggingFriends/PixelWorld)
is the durable public dataset. The
[PixelWorld dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html)
is served by the static Space
[HuggingFriends/PixelWorld-dashboard](https://huggingface.co/spaces/HuggingFriends/PixelWorld-dashboard).
GitHub holds reproducible code and protocol/configuration templates.

The current suite is Physics-IQ Verified 198, RBench Common Manipulation 50,
PhyGenBench T2V 160 and PCVE-RigidBench full 129. RoboJudge is historical.
See [the current protocol](MAIN_SUITE_20261124.md).

The public repository cards are tracked as [HF_DATASET_README.md](HF_DATASET_README.md)
and [HF_SPACE_README.md](HF_SPACE_README.md). Their protocol source is
`configs/hf-publication-protocol.json`. After reviewing those sources, publish
only the documentation and verify it at the returned commit revisions:

```bash
python -m tools.publish_hf_readmes --dry-run
python -m tools.publish_hf_readmes --token-file "$HF_TOKEN_FILE"
```

This command preserves the static Space settings, updates its README and the
dataset README/protocol metadata, and records byte-for-byte readback receipts.
It does not upload or alter experiment videos, traces or generation results.

## Credentials and private storage

Use a private HF token file with write access to both public repositories.
The case/progress uploaders require `--token-file`. The main-suite publisher
uses the standard HF authentication (`HF_TOKEN` or a locally cached login).
Never place a credential in a public payload or checked-in config.

Original raw API requests/responses and run artifacts belong in the private
LF3data archive described by [STORAGE_LAYOUT.md](STORAGE_LAYOUT.md). Public
trace bundles preserve the recorded native events, tool arguments/results,
returned images, code revisions and input/output text after removing secrets
and private paths. They exclude licensed standalone benchmark source files,
evaluator references/masks, ground truth and full generated RGB tensors.
Public exports accompany the originals; they do not replace private archives.

## Dataset layout

```text
README.md                                  dataset entry point
metadata/protocol.json                     protocol provenance
metadata/experiment_index.json             current suite/model/analysis index
metadata/main_suite_status.json            current main-suite snapshot
metadata/publication_coverage.json         current hashes vs upload receipts
dashboard/index.html                       HTML paired with the main snapshot
live/catalogue.json                        live case/group progress
live/publication_coverage.json             same publication coverage
live/<suite>/<model>/<track>/index.json     case and attempt index
live/<suite>/<model>/<track>/<case>.json    sanitized case/attempt detail
live/<suite>/<model>/<track>/<case>.trace.tar.gz
live/evaluation.json                       available evaluation records
data/assets/<hash>/preview.mp4             playable generated candidate
data/assets/<hash>/poster.jpg              preview poster
data/                                      retained bulk snapshots
traces/                                    legacy benchmark/model archives
baselines/                                 baseline evidence
```

Use the detail path recorded in the index: the case basename can identify an
attempt. Each closed case keeps its manifest, recorded usage, cleaned tool
events and runnable code. Failed attempts retain their trace without a
fabricated final video. New individual traces use the `live/` path above;
`traces/` is the older bulk archive layout.

## Export and publish

Set `$BUNDLE` to the durable run/publication bundle containing the owner's
`live_sources.json` registry, and `$NIGHTLY_ROOT` to the current main-suite
control root. These may differ. `$HF_TOKEN_FILE` identifies a private token
file. Create the ledger directory before starting the workers:

```bash
mkdir -p "$BUNDLE/state"
export PUBLICATION_SOURCE="$BUNDLE/publication/pixelworld"
```

Run the following long-running commands as separate supervised services, with
logs and ledgers on durable storage. Start the exporter first; the other
workers consume its output as it becomes ready.

### 1. Refresh the sanitized publication bundle

```bash
python -m tools.live_benchmark_data \
  --run-root "$BUNDLE" \
  --checkout "$BUNDLE/publication" \
  --local-only --interval 60
```

The exporter reads `$BUNDLE/live_sources.json` and writes
`$PUBLICATION_SOURCE/catalogue.json`, group details and previews/posters.
`--local-only` prevents Git publication. Use `--once` for one export pass.

Source projections must preserve the `cases` total in `status.json` so the
exporter can discover older deliveries as well as currently running cases.
For independently exported immutable case records, add
`--external-cache "$BUNDLE/accepted_cache_import.json"`; this option may be
repeated. The exporter merges these records on every pass, preserving its
current live entries even when an importer finishes during a cache write.

### 2. Upload closed cases, media and traces

```bash
python -m tools.publish_hf_incremental \
  --source "$PUBLICATION_SOURCE" \
  --state "$BUNDLE/state/hf_incremental.json" \
  --token-file "$HF_TOKEN_FILE" \
  --batch-size 8 --interval 60
```

Each cycle packages up to eight ready closed records and publishes case JSON,
its playable media and sanitized trace in an atomic dataset commit. A case
waits while its preview or required export is unavailable. The local ledger
records content hashes, trace format and commit receipts; matching records are
not uploaded again. Running attempts remain on the progress channel.

For a backlog of already accepted videos, add `--priority-group` with the exact
`suite/model/track` identity from the catalogue. The flag can be repeated:

```bash
python -m tools.publish_hf_incremental \
  --source "$PUBLICATION_SOURCE" \
  --state "$BUNDLE/state/hf_incremental.json" \
  --token-file "$HF_TOKEN_FILE" \
  --batch-size 32 --interval 60 \
  --priority-group physics_iq_res1080/deepseek-v41/vision-agent \
  --priority-cases-per-group 8
```

Accepted-delivery rounds take up to eight ready successes from each named group,
within the overall batch limit. Alternating history rounds retain the normal
group rotation, including failed traces. Missing previews still wait; the flags
do not generate videos, change accepted-run selection or bypass trace export.
Without priority groups, the existing upload rotation is unchanged.

### 3. Mirror progress and publication coverage

```bash
python -m tools.watch_hf_dataset_progress \
  --source "$PUBLICATION_SOURCE" \
  --state "$BUNDLE/state/hf_progress_watcher.json" \
  --token-file "$HF_TOKEN_FILE" \
  --interval 180
```

This updates the dataset's live progress and coverage JSON independently of
media uploads. Coverage joins the current indexed detail hashes to completed
HF upload receipts, including the required trace format. It is **not a full
remote byte audit**. Pending uploads, missing traces and generation progress
remain distinguishable.

### 4. Publish the main-suite snapshot, then the Space

Authenticate the main publisher without copying a credential into the bundle:

```bash
export HF_TOKEN="$(cat "$HF_TOKEN_FILE")"
python -m tools.publish_main_suite_hf \
  --nightly "$NIGHTLY_ROOT" \
  --publication "$PUBLICATION_SOURCE" \
  --state "$NIGHTLY_ROOT/main_suite_hf_publisher.json" \
  --watch-seconds 60
```

The publisher first commits `metadata/main_suite_status.json`,
`metadata/experiment_index.json` and `dashboard/index.html` to the dataset
atomically, then reads back their exact bytes at the returned commit revision.
After successful dataset readback, it updates the Space's `index.html` and
`main_suite_status_v2.json`. Separate dataset/Space receipts record the commit
identities. The watcher checks every minute and uploads changed content
only. Omit `--watch-seconds` for one pass; `--dry-run` validates a snapshot
without uploading it.

## Open-tab refresh behavior

The dashboard polls the **dataset** `metadata/main_suite_status.json` every
60 seconds. On a newer `updated_utc`, it fetches the paired dataset
`dashboard/index.html` and replaces the page's `<main>` content in place.
It waits for the HTML timestamp to match the status timestamp if propagation
is delayed. This does not reload the whole browser tab. Relative-time labels
refresh every 15 seconds. Space build propagation and media upload progress
have their own timing; a fresh snapshot does not imply every trace is public.

## Verify and recover

Check the public dataset
[status](https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/metadata/main_suite_status.json),
[index](https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/metadata/experiment_index.json)
and [coverage](https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/metadata/publication_coverage.json).
The index's `snapshot_utc` and the paired HTML timestamp should match the
status's `updated_utc`. After Space publication catches up,
[its status](https://huggingface.co/spaces/HuggingFriends/PixelWorld-dashboard/resolve/main/main_suite_status_v2.json)
should carry that timestamp too. Revision-specific dataset readback is the
publisher's snapshot proof; coverage is the case-upload ledger view.

Inspect a case index, then open its recorded video and `.trace.tar.gz` links.
Check pending counts and a sample archive's contents before declaring an
entire model published. Repository file counts alone cannot prove coverage.

- **429/rate limits:** retain the ledgers and let bounded workers resume. Dataset
  writers use the shared commit budget in `tools/hf_commit_budget.py`.
- **Stale table:** compare the public timestamp with the local catalogue and
  publisher receipts; check exporter and main-suite publisher health.
- **Missing media or trace:** inspect the case's export state and incremental
  upload ledger. Restore the source/export first, then resume uploads.
- **Dataset updated but Space pending:** resume the main-suite publisher from
  its receipts; it verifies the dataset before retrying Space publication.

Preserve established public paths and private originals. Storage reclamation
requires the verification rules in [STORAGE_POLICY.md](STORAGE_POLICY.md).
