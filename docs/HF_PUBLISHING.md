# Hugging Face publishing

PixelWorld publishes to two public Hugging Face repositories and keeps the
GitHub code repository free of generated media and private traces.

| Repository | Type | Contents |
|---|---|---|
| `HuggingFriends/PixelWorld` | dataset | progress JSON, per-case details, preview MP4/posters, sanitized trace archives, baseline evidence |
| `HuggingFriends/PixelWorld-dashboard` | static Space | dashboard HTML/CSS/JS, `main_suite_status_v2.json`, charts |

## Credentials

Use a Hugging Face token with write access to both repositories. Accepted
sources, in order: `HF_TOKEN`, `~/.cache/huggingface/token`, or an explicit
`--token-file`. Never commit the token, a private endpoint or a local absolute
path. The repository `.gitignore` and the publishers reject `/mnt/`, `/home/`,
`hf_*` and `ghp_*` strings in public payloads.

## Dataset layout

```text
README.md                       dataset entry point and protocol summary
metadata/protocol.json          frozen protocol snapshot
metadata/catalogue.json         model x benchmark progress and token means
metadata/main_suite_status.json current four-benchmark status mirror
live/catalogue.json             live progress used by the dashboard
live/<suite>/<model>/<track>/index.json
live/<suite>/<model>/<track>/<case>.json
live/evaluation.json            completed cohort scores
data/catalogue.json             entry point for case details
data/<suite>/<model>/<track>/index.json
data/<suite>/<model>/<track>/<case>.json
data/assets/<hash>/preview.mp4  compressed playable candidate
data/assets/<hash>/poster.jpg   480px thumbnail
traces/                         per-benchmark/model sanitized trace archives
baselines/                      baseline evidence
```

Each case JSON contains the prompt, task instruction, token usage, validation
summary, cleaned textual tool events, runnable `generate.py`, and candidate
media paths. Failed attempts keep their manifest and trace but never receive a
fabricated video.

Excluded by design: API keys, credentials, local absolute paths, benchmark
input first frames/source videos, Physics-IQ reference videos and masks, PCVE
target videos, evaluator ground truth, raw RGB tensors and per-frame PNG dumps.

## Publication chain

Run these three stages in order. Each stage is idempotent and resumes from its
own ledger.

### 1. Refresh the sanitized publication bundle

```bash
python -m tools.live_benchmark_data \
  --run-root "$BUNDLE" \
  --checkout "$BUNDLE/publication" \
  --local-only --interval 60
```

This scans the frozen run roots in `$BUNDLE/live_sources.json`, writes
`$BUNDLE/publication/pixelworld/{catalogue.json,<suite>/<model>/...}` and
renders previews/posters. `--local-only` keeps generated data out of Git.
Use `--once` for a single pass.

### 2. Upload closed cases, media and traces

```bash
python -m tools.publish_hf_incremental \
  --source "$BUNDLE/publication/pixelworld" \
  --state "$BUNDLE/state/hf_incremental.json" \
  --token-file "$HF_TOKEN_FILE" \
  --interval 60 --batch-size 2
```

Each cycle packages at most `--batch-size` closed cases and commits their
detail JSON, preview media and sanitized trace in one atomic dataset commit.
The ledger `state/hf_incremental.json` records what is already public.

### 3. Mirror progress JSON

```bash
python -m tools.watch_hf_dataset_progress \
  --source "$BUNDLE/publication/pixelworld" \
  --token-file "$HF_TOKEN_FILE" \
  --state "$BUNDLE/state/hf_progress_watcher.json" \
  --interval 180
```

This updates only `live/` progress JSON. It deliberately does not touch the
Space or the legacy frontend.

### 4. Publish the dashboard Space

```bash
python -m tools.publish_main_suite_hf \
  --nightly "$NIGHTLY_ROOT" \
  --publication "$BUNDLE/publication/pixelworld" \
  --state "$NIGHTLY_ROOT/main_suite_hf_publisher.json" \
  --watch-seconds 300
```

Only when the content digest changes does it commit `index.html` and
`main_suite_status_v2.json` to `HuggingFriends/PixelWorld-dashboard`.

## Dashboard refresh behavior

- The publisher rebuilds the snapshot every `--watch-seconds` (minimum 300) and
  commits only on a content change.
- The page embeds the snapshot's `updated_utc` in
  `<meta name="pixelworld-updated">` and polls `main_suite_status_v2.json`
  every 60 seconds. When the published snapshot changes, it reloads with a
  cache-busting query, so an open tab converges to the latest HF data without
  manual refreshes.
- The page does not poll the dataset directly; the Space is the HF-hosted
  dashboard origin. The dataset is the durable media/trace store.

## Verify

```python
from huggingface_hub import HfApi
api = HfApi()
print(len(api.list_repo_files('HuggingFriends/PixelWorld', repo_type='dataset')))
print(len(api.list_repo_files('HuggingFriends/PixelWorld-dashboard', repo_type='space')))
```

Check `main_suite_status_v2.json` in the Space and the dataset
`metadata/main_suite_status.json` for the same `updated_utc`.

## Troubleshooting

- **Rate limits / 429**: the publishers use bounded batches and a shared commit
  budget (`tools/hf_commit_budget.py`). Leave the watcher running; it resumes
  from the ledger.
- **Stale dashboard**: confirm the Space publisher is alive and that the
  publication bundle `catalogue.json` is fresh. A stale bundle produces a stale
  dashboard even if the Space commit succeeds.
- **Missing media**: `publish_hf_incremental` skips a case until its preview is
  rendered. Re-run stage 1, then the incremental publisher.
- **Never** rewrite or delete already published case paths; collaborators and
  the dashboard link to them. Add new snapshots instead.
