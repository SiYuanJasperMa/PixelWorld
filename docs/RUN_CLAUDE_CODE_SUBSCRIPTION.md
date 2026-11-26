# Claude Code on a Claude subscription: hand-off runbook

This route runs the four main Vision benchmarks with the pinned **Claude Code
2.1.304** client authenticated by the operator's own **Claude Max
subscription**. It sits alongside the Codex route in
[RUN_CLAUDE_MAIN.md](RUN_CLAUDE_MAIN.md) and does not replace it. Models used so far:
`claude-opus-5-5` (all four benchmarks) and `claude-fable-5-2` (Physics-IQ,
RBench and a 3-case demo of the other two). Effort is `high` for both.

Code: `pixelworld/controlled_harness.py` (`run_prepared_subscription`,
`host_subscription_token`), `pixelworld/messages_passthrough_relay.py`,
`pixelworld/claude_process_sandbox.py`, the audit changes in `pixelworld/audit.py`
and the `anthropic_subscription` provider in `pixelworld/config.py`. Tests:
`tests/test_messages_passthrough_relay.py` and `tests/test_claude_process_sandbox.py`.
`pixelworld/anthropic_responses_proxy.py` is a separate Codex-to-Messages bridge
and this route does not use it.

## 1. The route

| Item | Setting |
|---|---|
| Client | Claude Code 2.1.304. The binary path, version and sha256 are pinned in `locks.json` (`PIXELWORLD_CONTROLLED_LOCKS`) |
| Provider | `"type": "anthropic_subscription"`, `cli_backend: "claude_code"`, `env_key: "CLAUDE_CODE_OAUTH_TOKEN"`, `tool_mode` defaults to `native` |
| Credential | The OAuth access token of the host's own Claude Code login (`~/.claude/.credentials.json`), read by `host_subscription_token()`. If fewer than 300 s remain on the token, the host `claude` (`~/.local/bin/claude`) is run once to refresh it, under a file lock. The token value is never logged. The launchers `unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY` |
| Transport | A case-scoped verbatim Messages relay on loopback. Request and response bytes pass through unchanged. The relay drops any client `Authorization`/`x-api-key` header and injects `Authorization: Bearer <token>`, resolving the token **on each request**. Only `POST /v1/messages` and `POST /v1/messages/count_tokens` are forwarded; every other path is refused locally |
| Pinning | The relay refuses a `/v1/messages` request whose `model` or requested effort differs from the config, so the model cannot be silently substituted |
| Budget | `output_token_budget: 1000000` per case. After the budget is crossed, further model calls are refused without an outbound request |
| Records | `logs/native/messages_wire/NNNN/{input.body, response bytes, receipt.json}`, `native_launch.json`, `native_delivery.json`, `full_rollout.jsonl`. No credential headers are written |

### Native tools only

`--tools Bash,Read,Write,Edit,Glob,Grep --allowedTools <same> --permission-mode bypassPermissions`
with `--setting-sources '' --strict-mcp-config --disable-slash-commands
--no-session-persistence`. There is no WebFetch, WebSearch, Task/subagents, MCP,
skills, CLAUDE.md, cron or background tasks; these are disabled through
`CLAUDE_CODE_DISABLE_*` and `DISABLE_BUILTIN_AGENTS`. If an event projection
contains any other tool, it raises. The first frame (when present) is attached
as an image to the first user message.

### Whole-process sandbox (bwrap)

The whole Claude Code process tree runs inside one bubblewrap sandbox:

- new user, pid, net, ipc and uts namespaces, `--die-with-parent`, `--new-session`, and a fresh `/proc`;
- `/usr /bin /lib /lib64 /etc` are read-only. The pinned runtime is mounted at `/opt/runtime` and the Claude Code install tree at `/opt/claude-code`, both read-only;
- **`/workspace` (the case workspace) is the only writable host directory**;
- `HOME=/home/agent` is a private per-attempt directory. `/tmp` is a **private, empty, writable tmpfs**. `TMPDIR` points to `/workspace/.tmp`;
- **there is no network**. The only way out is a unix-socket bridge (bind-mounted read-only at `/run/pixelworld`) to this case's relay. Inside the sandbox it is exposed as `127.0.0.1:8787`;
- the environment is cleared. The client only holds a placeholder token (`pixelworld-placeholder-not-a-credential`);
- Bash timeout is 10 min (`BASH_DEFAULT_TIMEOUT_MS = BASH_MAX_TIMEOUT_MS = 600000`, the same as the Codex main protocol). Background bash is off.

## 2. Owner decisions (2026-11-25/26)

1. **`/tmp` counts as case scratch.** It is a per-case empty tmpfs inside the sandbox.
   `run_prepared_subscription` sets `PIXELWORLD_PRIVATE_TMP=1`, which makes the
   audit treat `/tmp/**` as inside the case. This supersedes the "/tmp read-only"
   note in the first infra-exclusion entry (physics_iq 0002), which describes an
   intermediate state.
2. **The static command-path audit is recorded but not enforced on this route.**
   `PIXELWORLD_SANDBOX_ENFORCED=1` moves flagged command-text paths to
   `sandbox_waived_command_paths` in the validation report. Real file reads and
   changes are still audited.
3. **ONE real attempt per case**: `max_attempts = max_total_attempts = 1`. An
   infrastructure failure (harness defect, operator interrupt, an auth failure
   before any model call) is not a model attempt. Move its attempt directory out
   of the runs root into `$PIXELWORLD_HOME/archive/infra_excluded/` and add one
   line to that folder's `README.md` with the reason. The matrix then reruns the
   case. Current entries: physics_iq 0002 (/tmp policy), phygenbench phygen_003
   and rbench 0005 (operator interrupts), `token_expiry_20261125/` (OAuth refresh bug).
4. **Stop at 90 % of the weekly quota.** The remaining 10 % is reserved for the operator (see the guard below).

## 3. Operator layout and setup

All tooling reads `PIXELWORLD_HOME` (default `$HOME/pixelworld_opus`):

```
$PIXELWORLD_HOME/
  PixelWorld/            this checkout + .venv       (PIXELWORLD_REPO)
  configs_claude/        rendered configs + scheduling_limits.json (PIXELWORLD_CONFIGS)
  locks.json             pinned client lock
  runtime/               pinned case runtime (bin/python, bin/bwrap, lib/), owner-supplied
  cc2304/                npm install of @anthropic-ai/claude-code@2.1.304
  inputs/  sources/      staged inputs and their official sources
  runs_claude/  runs_claude_fable/   Opus / Fable runs roots
  hf_publish/            HF bundle state (ledgers, logs, publication export)
  archive/infra_excluded/
```

1. Log in with the host Claude Code (`claude`, Max plan) as the operator. Check that `~/.claude/.credentials.json` exists.
2. Install the client: `cd $PIXELWORLD_HOME/cc2304 && npm install @anthropic-ai/claude-code@2.1.304`.
   Then check `sha256sum` of `node_modules/@anthropic-ai/claude-code/bin/claude.exe`
   against `configs/claude-code/locks.example.json`.
3. Render the templates. They use the `<PIXELWORLD_HOME>` placeholder:
   ```bash
   mkdir -p "$PIXELWORLD_HOME/configs_claude"
   for f in configs/claude-code/*.json; do
     sed "s|<PIXELWORLD_HOME>|$PIXELWORLD_HOME|g" "$f" > "$PIXELWORLD_HOME/configs_claude/$(basename "$f")"; done
   mv "$PIXELWORLD_HOME/configs_claude/locks.example.json" "$PIXELWORLD_HOME/locks.json"
   ```
   Templates: `*_smoke5.json` (first 5 official cases, Opus), `*_main_opus.json`,
   `physics_iq|rbench_main_fable.json`, `rbench|phygenbench|pcve_demo_fable.json`
   (first 3 cases), and `scheduling_limits.json` (per-model caps: Opus 8, Fable 2;
   `paused_models_by_config` is written by the guard). Smoke and main share a runs
   root, so accepted smoke cases are reused.
   **Protocol labels:** RBench, PhyGenBench and PCVE configs still carry
   `DRAFT-OWNER-CONFIRM-…` protocol labels, because the owner's frozen labels are
   not in the repo. Keep them as they are. The owner confirms or relabels them at
   publication. Physics-IQ uses `pixelworld-physics_iq_720p24fps-native-source-v1`.

## 4. Inputs (staged from official public sources)

Recorded in `$PIXELWORLD_HOME/STAGING_MANIFEST.json`. It is owner-held and lists
sha256 for 1,043 source files and 2,099 staged input files. Paths are relative
to `$PIXELWORLD_HOME`. `tools/…` means this checkout.

| Benchmark | Source (revision) | Staging command |
|---|---|---|
| Physics-IQ (198) | Switch frames from the public GCS bucket `storage.googleapis.com/physics-iq-benchmark/switch-frames/`. Every sha256 equals `source_sha256` in `data/physics_iq_verified_i2v.json` (pinned HF Physics-IQ-Verified@b65f567d, which is gated). Code and descriptions from github.com/google-deepmind/physics-IQ-benchmark @ d3f3de16 | `pixelworld physics-iq setup-inputs --inputs-root sources/physics_iq/bpp256/inputs --upstream sources/physics_iq/physics-IQ-benchmark --source-dir sources/physics_iq/gcs`, then `python tools/prepare_physics_iq_4k.py --source-frames sources/physics_iq/gcs/switch-frames --base-inputs sources/physics_iq/bpp256/inputs --inputs inputs/physics_iq --width 1280 --height 720` |
| RBench Common Manipulation (50) | HF `DAGroup-PKU/RBench` @ 6bdccf34: `imgs/common_manipulation/*.jpg`, `prompts/common_manipulation_prompts.json` | `python -m tools.stage_rbench_common --source sources/rbench/common_manipulation --output inputs/rbench` |
| PhyGenBench (160) | github.com/OpenGVLab/PhyGenBench @ f8642cb7: `PhyGenBench/prompts.json` (byte copy) | `python tools/prepare_phygenbench.py --source sources/phygenbench/prompts_upstream.json --output sources/phygenbench/base_720p24/inputs --provenance inputs/phygenbench_provenance.json --width 1280 --height 720 --frames 120 --fps 24`, then `python tools/prepare_photoreal_t2v.py --source-config sources/phygenbench/photoreal_step/base_config.json --inputs-root inputs/phygenbench --runs-root sources/phygenbench/photoreal_step/unused_runs --output-config sources/phygenbench/photoreal_step/photoreal_config.json` |
| PCVE-RigidBench (129) | HF `ccmoony/PCVE-RigidBench` @ 772f7b50: `benchmark_manifest.json` + 129 source videos (via `pixelworld.pcve.fetch`) | `python -m tools.setup_pcve prepare --data-root sources/pcve/PCVE-RigidBench --inputs-root inputs/pcve --long-edge 1280` |

## 5. Running

Scripts are in `tools/claude_code/`. They all source `env.sh` (activates the venv,
sets `PIXELWORLD_CONTROLLED_RUNTIME/LOCKS`, unsets API credentials).

```bash
tools/claude_code/run_smoke.sh physics_iq          # 5 cases; then rbench, phygenbench, pcve
nohup tools/claude_code/run_main.sh opus  > main_opus.log  2>&1 &   # physics_iq→rbench→phygenbench→pcve
nohup tools/claude_code/run_main.sh fable > main_fable.log 2>&1 &   # physics_iq→rbench
nohup tools/claude_code/run_fable_demo.sh > demo_fable.log 2>&1 &   # 3 cases × rbench/phygenbench/pcve
OPUS_BENCHES="pcve" tools/claude_code/run_main.sh opus             # restart from a given bench
pixelworld matrix status --config "$PIXELWORLD_CONFIGS/rbench_main_opus.json"
```

**Quota/memory/token guard** (keep it running during all runs):
`nohup tools/claude_code/guard.py &`. Every 10 min (every 1 min while degraded),
it reads `claude -p /usage`. At ≥90 % of the weekly quota for all models it pauses
both models; at ≥90 % of the Fable weekly quota it pauses Fable. It also pauses
both models when the quota is unreadable, the host token refresh fails, or
MemAvailable is below 5 GB. Pausing writes `paused_models_by_config` in
`scheduling_limits.json`. Running cases finish and no new cases are dispatched.
Log: `$PIXELWORLD_HOME/guard.log`.

**Stopping.** When a matrix ends, `run_main.sh` starts the next benchmark. To stop:
(1) kill the driver bash (`run_main.sh`/`run_fable_demo.sh`) **first**, then
(2) `pixelworld matrix stop --config <active config>`, which writes `STOP` in the
runs root. Running cases are not killed by this. Doing it in the other order
starts the next benchmark.

**Cost report:** `tools/claude_code/cost_report.py [runs_root]` sums the relay
receipts per case. It reports the API-equivalent dollar cost at Opus 5.5 list
prices per MTok: input $4, output $20, cache read $0.20, cache write $5 (5-min)
or $8 (1-hour). The subscription is not billed per token, so this is an
equivalence estimate only.

## 6. HF publishing

`tools/claude_code/hf_publish/run_publish.sh` publishes to the dataset
`HuggingFriends/PixelWorld` with a contributor-scoped bundle
(`BUNDLE=$PIXELWORLD_HOME/hf_publish`). The token is read from `HF_TOKEN_FILE`
(default `~/.cache/huggingface/token`) and never from the repo.

```bash
tools/claude_code/hf_publish/run_publish.sh dry-run        # export + package + leak scan, no network writes
GO_LIVE=1 tools/claude_code/hf_publish/run_publish.sh start
tools/claude_code/hf_publish/run_publish.sh status | verify | stop
```

- **Suite keys** are `<suite>_<model>` (`sync_sources.py` builds per-model shadow roots), e.g.
  `physics_iq_720p24fps_claude-opus-5-5`, `rbench_common_manipulation_claude-fable-5-2`,
  `phygenbench_t2v_main720p24fps_…`, `pcve_standard720p24fps_…`.
- **Commit rate.** HF caps commits at 128/hour per repo, and that budget is shared
  with the owner's publisher. Stay at or below about 10 commits/hour. The defaults
  are uploader every 900 s (batches of up to 40 cases, ≤600 MB per commit) and
  progress every 1800 s, about 6 commits/hour. Commits go through `tools.hf_commit_budget`.
- **Leak gate.** `publish_incremental_receipted.py` blocks any commit containing
  `sk-ant-…`, `hf_…`, `/home/<user>/`, `/mnt/`, a non-redacted `Bearer`, `ghp_` or
  the operator root name.
- **Read-back before cleanup.** `verify_published.py` downloads the metadata of
  every committed file at the commit and on `main` (size + sha256 or git-sha1).
  Only then does it mark a case `published` in `state/published_ledger.json`.
  `offload.py` moves only `published`, finished run dirs.
- **ffmpeg guard.** `bin/ffmpeg_guard.py` (`PIXELWORLD_FFMPEG`) waits for frame
  PNGs to be structurally complete and fails loudly on decode errors instead of
  writing short previews. Log: `logs/ffmpeg_guard.log`.
- `scoped_progress.py` writes only `live/<suite>/<model>/vision-agent/index.json`.
  It never touches the global catalogue.

**Offload.** `PIXELWORLD_OFFLOAD_DIR=/path/to/disk tools/claude_code/offload.py` copies
each verified run dir, diffs it, replaces it with a symlink, and logs to
`$PIXELWORLD_HOME/offload.log`. Offload is disabled when the variable is unset.
`tools/claude_code/archive_to_ssd.sh <dir> <dest.tar>` tars a directory, verifies
it, then removes the source.

## 7. Known issues and lessons

- **OAuth refresh window (fixed).** On 2026-11-25 cases were launched while the
  host refresh check failed because its threshold was too strict. They died before
  any model call and were excluded as infrastructure (`token_expiry_20261125/`).
  The fix: the token is resolved on each request in the relay, with a 5-minute
  refresh window, and the guard pauses dispatch when the refresh fails.
- **HF 429.** Over-frequent commits hit the hourly limit. `hf_commit_budget` honours
  the retry window. Keep the intervals above and do not add other committers.
- **exFAT is case-insensitive.** RBench `case_0001_attempt_01` contains both
  `g_A.png` and `g_a.png`. Copying it to the exFAT SSD failed with "File exists",
  so it is kept local (offload records it as failed and skips it).
- **`run_main.sh` advances to the next benchmark** when a matrix ends. Stop it with the order in §5.
- **Dashboard main table.** The scoped publisher only writes per-suite indexes.
  The owner must add the Claude models/suites to the global catalogue and
  main-suite snapshot (`tools.publish_main_suite_hf`, owner-only) before they
  appear in the dashboard's main table.

## 8. Open items for the owner

- Confirm or replace the `DRAFT-OWNER-CONFIRM-…` protocol labels (RBench, PhyGenBench, PCVE).
- Add the Claude suites to the dashboard main table.
