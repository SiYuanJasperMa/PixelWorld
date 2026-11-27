#!/usr/bin/env bash
# Publish PixelWorld Claude main-suite results to HF dataset HuggingFriends/PixelWorld,
# following docs/HF_PUBLISHING.md with a contributor-scoped bundle.
#
#   ./run_publish.sh dry-run        (default) export locally + package uploads, NO network writes
#   GO_LIVE=1 ./run_publish.sh start   start the long-running workers (uploads!)
#   ./run_publish.sh status | stop | verify | scan
#
# Environment: PIXELWORLD_HOME (default $HOME/pixelworld_opus), BUNDLE (state/output dir,
# default $PIXELWORLD_HOME/hf_publish), PIXELWORLD_REPO, RUNS, MODELS, HF_TOKEN_FILE.
# Commit rate: uploader every 900 s + progress every 1800 s (~6 commits/hour). Keep the
# total at or below ~10/hour: HF allows 128 commits/hour per repo, shared with the owner's publisher.
#
# Pipeline (one process per ledger; all ledgers/logs under $BUNDLE/state, $BUNDLE/logs):
#   0 sync     bin/sync_sources.py        per-model shadow roots + live_sources.json (read-only on runs)
#   1 export   tools.live_benchmark_data  --local-only  -> publication/pixelworld (sanitized details, previews)
#   2 upload   bin/publish_incremental_receipted.py  = tools.publish_hf_incremental.publish_cycle
#              + per-file sha256/git-sha1 receipts + fail-closed leak gate
#   3 progress bin/scoped_progress.py     only live/<our suite>/<model>/vision-agent/index.json
#              (NOT tools.watch_hf_dataset_progress: it overwrites global catalogue/coverage files)
#   4 verify   bin/verify_published.py    readback size+hash at commit and on main -> published_ledger.json
#   (step 4 of the docs, tools.publish_main_suite_hf, is owner-only: see README notes in the report)
set -euo pipefail

PIXELWORLD_HOME="${PIXELWORLD_HOME:-$HOME/pixelworld_opus}"
BIN="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/bin"   # helper scripts ship with the repo
BUNDLE="${BUNDLE:-$PIXELWORLD_HOME/hf_publish}"
REPO="${PIXELWORLD_REPO:-$PIXELWORLD_HOME/PixelWorld}"
# Opus runs live in runs_claude, Fable runs in runs_claude_fable (space-separated list).
RUNS="${RUNS:-$PIXELWORLD_HOME/runs_claude $PIXELWORLD_HOME/runs_claude_fable}"
MODELS="${MODELS:-claude-opus-5-5 claude-fable-5-2}"
PY="$REPO/.venv/bin/python"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-${HF_HOME:-$HOME/.cache/huggingface}/token}"
STATE="$BUNDLE/state"; LOGS="$BUNDLE/logs"
export PUBLICATION_SOURCE="$BUNDLE/publication/pixelworld"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export HF_HUB_DISABLE_XET=1 HF_HUB_DISABLE_PROGRESS_BARS=1
# Exporter preview encoder: waits for frame PNGs to be complete/stable before calling
# ffmpeg and logs any decode failure with the offending files (logs/ffmpeg_guard.log).
export PIXELWORLD_FFMPEG="${PIXELWORLD_FFMPEG:-$BIN/ffmpeg_guard.py}" FFMPEG_GUARD_LOG="$LOGS/ffmpeg_guard.log"
mkdir -p "$STATE" "$LOGS"
ORIG_PWD="$PWD"
cd "$REPO"   # python -m tools.* resolves against the checkout

need_token() {
  [[ -r "$HF_TOKEN_FILE" && -s "$HF_TOKEN_FILE" ]] || { echo "HF token file not readable: $HF_TOKEN_FILE" >&2; exit 2; }
}

sync_once() { "$PY" "$BIN/sync_sources.py" --bundle "$BUNDLE" --runs $RUNS --models $MODELS; }

scan() {  # grep a staged directory (+ extracted traces) for secrets / private paths
  local dir x; dir="$(cd "$ORIG_PWD" && realpath "$1")"; x="$(mktemp -d "$STATE/scan.XXXXXX")"
  ( cd "$dir" && find . -name '*.trace.tar.gz' | while read -r t; do
      d="$x/$(echo "$t" | tr / _)"; mkdir -p "$d"; tar xzf "$t" -C "$d"; done )
  local bad=0
  for pat in 'sk-ant' 'hf_[A-Za-z0-9]{20,}' 'oauth' "$HOME/" '/home/' 'Bearer [^[]' 'ghp_' 'pixelworld_opus' '/mnt/'; do
    n=$( { /usr/bin/grep -rlEI -- "$pat" "$dir" "$x" 2>/dev/null || true; } | wc -l)
    printf '%-24s %s files\n' "$pat" "$n"; [[ $n -eq 0 ]] || bad=1
  done
  echo "(expected benign: none; 'oauth' is case-sensitive lowercase - env var name CLAUDE_CODE_OAUTH_TOKEN is uppercase)"
  echo "extracted traces kept in $x for inspection"
  return $bad
}

start_worker() {  # name, command...
  local name="$1"; shift
  local pidf="$STATE/$name.pid"
  if [[ -f "$pidf" ]] && kill -0 "$(cat "$pidf")" 2>/dev/null; then echo "$name already running (pid $(cat "$pidf"))"; return; fi
  nohup "$@" >>"$LOGS/$name.log" 2>&1 &
  echo $! >"$pidf"; echo "started $name pid $!"
}

case "${1:-dry-run}" in
dry-run)
  DRY="$STATE/dryrun/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$DRY"   # fresh ledger => full package view
  sync_once
  # 1. Exporter: the official loop only merges rendered cases on the *next* cycle,
  #    so --once never writes details. Run it bounded instead.
  DRY_EXPORT_SECONDS="${DRY_EXPORT_SECONDS:-300}"
  echo "exporter (local-only) for ${DRY_EXPORT_SECONDS}s ..."
  timeout "$DRY_EXPORT_SECONDS" "$PY" -m tools.live_benchmark_data --run-root "$BUNDLE" \
      --checkout "$BUNDLE/publication" --local-only --interval 15 >>"$LOGS/exporter.dryrun.log" 2>&1 || true
  cat "$BUNDLE/publication/.publisher_status.json"; echo
  # 2. Package exactly what the uploader would commit (no HfApi, no network).
  "$PY" "$BIN/publish_incremental_receipted.py" --source "$PUBLICATION_SOURCE" \
      --state "$STATE/hf_incremental.json" --receipts "$STATE/upload_receipts.jsonl" \
      --dry-run --dry-run-dir "$DRY" --batch-size 8 | tail -1
  # 3. Progress indexes that would be uploaded.
  "$PY" "$BIN/scoped_progress.py" --source "$PUBLICATION_SOURCE" --bundle "$BUNDLE" \
      --state "$DRY/hf_progress_scoped.json" --dry-run
  echo "staged payload: $DRY/staged  (receipts: $DRY/upload_receipts.dryrun.jsonl)"
  scan "$DRY/staged"
  ;;
start)
  [[ "${GO_LIVE:-0}" == 1 ]] || { echo "Refusing: set GO_LIVE=1 to start uploading to HuggingFriends/PixelWorld" >&2; exit 2; }
  need_token
  start_worker sync      "$PY" "$BIN/sync_sources.py" --bundle "$BUNDLE" --runs $RUNS --models $MODELS --interval 30
  sleep 2
  start_worker exporter  "$PY" -m tools.live_benchmark_data --run-root "$BUNDLE" \
                           --checkout "$BUNDLE/publication" --local-only --interval 60
  start_worker uploader  "$PY" "$BIN/publish_incremental_receipted.py" --source "$PUBLICATION_SOURCE" \
                           --state "$STATE/hf_incremental.json" --receipts "$STATE/upload_receipts.jsonl" \
                           --token-file "$HF_TOKEN_FILE" --batch-size 40 --interval 900
  start_worker progress  "$PY" "$BIN/scoped_progress.py" --source "$PUBLICATION_SOURCE" --bundle "$BUNDLE" \
                           --state "$STATE/hf_progress_scoped.json" --token-file "$HF_TOKEN_FILE" --interval 1800
  start_worker verifier  bash -c "while true; do '$PY' '$BIN/verify_published.py' --state-dir '$STATE' \
                           --bundle '$BUNDLE' --token-file '$HF_TOKEN_FILE' || true; sleep 600; done"
  ;;
verify)
  need_token
  "$PY" "$BIN/verify_published.py" --state-dir "$STATE" --bundle "$BUNDLE" --token-file "$HF_TOKEN_FILE"
  "$PY" - "$STATE/published_ledger.json" <<'EOF'
import json, sys, collections
led = json.load(open(sys.argv[1]))
print(collections.Counter(v['status'] for v in led.values()))
EOF
  ;;
status)
  for n in sync exporter uploader progress verifier; do
    f="$STATE/$n.pid"; if [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null; then s=running; else s=stopped; fi
    printf '%-10s %s\n' "$n" "$s"
  done
  cat "$BUNDLE/publication/.publisher_status.json" 2>/dev/null; echo
  tail -n1 "$LOGS/uploader.log" "$LOGS/progress.log" "$LOGS/verifier.log" 2>/dev/null || true
  "$PY" -c "import json;print(json.load(open('$STATE/publication_coverage.json'))['totals'])" 2>/dev/null || true
  ;;
stop)  # stops only our own workers (pidfiles); never touches experiment processes
  for n in verifier progress uploader exporter sync; do
    f="$STATE/$n.pid"; [[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null && echo "stopped $n" || true
  done
  ;;
scan) scan "${2:?staged dir, e.g. $STATE/dryrun/<stamp>/staged}" ;;
*) echo "usage: $0 [dry-run|start|status|stop|verify|scan [dir]]" >&2; exit 2 ;;
esac
