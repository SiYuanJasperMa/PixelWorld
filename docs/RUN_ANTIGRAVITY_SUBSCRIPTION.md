# Antigravity CLI (agy 1.5.18) on a Google AI subscription: runbook

This route runs the four main Vision benchmarks with `gemini-4-neon-high` through the
pinned **Antigravity CLI 1.5.18** (`agy`), logged in with the operator's Google AI
Pro/Ultra account. It mirrors the Claude Code subscription route
([RUN_CLAUDE_CODE_SUBSCRIPTION.md](RUN_CLAUDE_CODE_SUBSCRIPTION.md)); the differences
are listed in section 4.

Code: `pixelworld/antigravity_harness.py` (ACP driver, projection, rollout,
`run_prepared_antigravity`), `pixelworld/antigravity_sandbox.py` (bwrap),
`pixelworld/cloudcode_relay.py` (recording relay), `pixelworld/egress_allowlist_proxy.py`,
the `google_subscription` provider / `antigravity` backend in `pixelworld/config.py`,
dispatch in `controlled_harness.run_prepared`. Tests: `tests/test_antigravity_route.py`.

## 1. Setup

1. Log in once with the host agy (`agy`, interactive). The login is stored as
   `~/.gemini/antigravity-cli/antigravity-oauth-token` (file storage; agy uses the OS
   keyring only when a D-Bus session is present, and the sandbox has none).
   `PIXELWORLD_AGY_STATE` overrides that directory.
2. Pinned binary: `$PIXELWORLD_HOME/agy1518/agy` (read-only copy of agy 1.5.18), recorded
   in `locks.json` under `harnesses.antigravity` (`version` 1.5.18 + sha256). Auto-update
   cannot change it: it is mounted read-only and the sandbox sets `AGY_CLI_DISABLE_AUTO_UPDATE=1`.
3. Configs: `$PIXELWORLD_HOME/configs_gemini/{physics_iq,rbench,phygenbench,pcve}_{smoke5,main}.json`
   (copies of the Claude configs; model `gemini-4-neon-high`, `cli_backend: antigravity`,
   `cli_version: 1.5.18`, `provider.type: google_subscription`, runs root
   `runs_gemini/<bench>`, `max_attempts = max_total_attempts = 1`, concurrency 4).

```bash
cd $PIXELWORLD_HOME/PixelWorld && . .venv/bin/activate
export PIXELWORLD_CONTROLLED_RUNTIME=$PIXELWORLD_HOME/runtime PIXELWORLD_CONTROLLED_LOCKS=$PIXELWORLD_HOME/locks.json
pixelworld run-case --config $PIXELWORLD_HOME/configs_gemini/physics_iq_smoke5.json \
    --model gemini-4-neon-high --track vision-agent --case 0001
pixelworld matrix run --config $PIXELWORLD_HOME/configs_gemini/physics_iq_main.json
```

## 2. The route

| Item | Setting |
|---|---|
| Interface | `agy acp` (Agent Client Protocol, stdio JSON-RPC) driven by `AcpDriver`: `initialize` → `session/new` (cwd `/workspace`, no MCP servers) → one `session/prompt` |
| First frame | ACP `image` content block next to the task text in the first user message; agy sends it to the model as `inlineData` (verified in the wire) |
| Model / effort | `gemini-4-neon-high`; effort is part of the slug (wire: `thinkingConfig.thinkingLevel = HIGH`; upstream `modelVersion = gemini-4-neon`). `session/new` must report `currentModelId == gemini-4-neon-high` (else `session/set_model`; failure stops the case) |
| Transport | `--cloud_code_endpoint http://127.0.0.1:8788` → case-scoped recording relay → `https://daily-cloudcode-pa.googleapis.com`. Verbatim; only allowlisted Cloud Code methods are forwarded |
| Pinning | The relay refuses any model call whose `model` is not `gemini-4-neon-high` or whose `requestType` is not `custom_agent`/`agent`. agy's auxiliary calls (title generation `checkpoint` on gemini-3.5-flash-lite, tool safety rater `agent_monitoring` on flash-lite / hydrogen-sonar) are therefore refused locally; agy continues without them |
| Budget | 1,000,000 reported output tokens per attempt (`candidatesTokenCount + thoughtsTokenCount` of each response = agy's own `output_tokens`, which already include `thinking_tokens`). Charged per model response in the relay; once crossed, model calls are refused (429) and the driver cancels/kills agy (`stop_reason = output_token_budget_exhausted`) |
| Records | `logs/native/cloudcode_wire/NNNN/{input.body,response.raw,receipt.json}` (model calls), session-method receipts (size + sha256 only; their bodies hold the account e-mail), `egress.jsonl`, `events.native.jsonl` (raw ACP), `native_launch.json`, `native_delivery.json` (incl. ACP-reported usage and session/model info), `full_rollout.jsonl`, `tool_calls.json` (incl. returned images) |

### Tools

A user-level custom agent (`~/.gemini/config/agents/pixelworld.md` in the private HOME,
mounted read-only) limits the model-visible tools to `read_file`, `run_command`,
`write_file`, `edit_file` plus agy's mandatory `manage_task` (background commands). Its
system prompt body is agy's default `<identity>` text verbatim; agy then appends its own
user_information/messaging/artifacts/guidelines/communication_style sections. No
`search_web`, browser, subagents, `run_workflow`, swarm, schedule, skills, plugins or MCP
(`--include-default-customizations=false`, no MCP config). The tool declarations of every
forwarded request are checked (`model_declared_tools`); an extra tool fails the attempt.
The projection raises on any tool outside the allowlist.

### Isolation

* **Whole process (bwrap)**: user/pid/net/ipc/uts namespaces, fresh /proc, cleared env,
  ro system dirs, pinned runtime at `/opt/runtime`, agy at `/opt/agy`, `/workspace` the only
  writable case path, private `/tmp` tmpfs, HOME `/home/agent` = per-attempt directory under
  `~/.cache/pixelworld-agy/attempt-*` (**outside the run dir**, deleted after the attempt)
  holding only copies of `antigravity-oauth-token` and `cache/onboarding.json` plus the
  generated `settings.json` and agent file (both ro-mounted).
* **Network**: none, except two unix-socket bridges: 8788 → Cloud Code relay, 8787 →
  CONNECT proxy allowing only `daily-cloudcode-pa`, `cloudcode-pa`, `oauth2`, `www`
  `.googleapis.com` (443). Telemetry (`play.googleapis.com`), experiments (`aicode.googleapis.com`)
  and avatars are refused and logged.
* **Agent commands** additionally run in agy's terminal sandbox (`enableTerminalSandbox`,
  `--sandbox`): own network namespace (no access to the bridges, no DNS), the agy app dir
  and therefore the token copy are hidden, workspace + /tmp writable.

## 3. Verified (case 0001 and sandbox probes)

From inside the agent's shell: cannot read the token copy, the host home, `~/.claude`,
`$PIXELWORLD_HOME`, other case runs; cannot reach 8787/8788, agy's language server, 8.8.8.8
or DNS; `curl https://example.com` gets only agy's internal sandbox proxy which fails
(no route; nothing appears in `egress.jsonl`). Pinned `/opt/runtime/bin/python3` with numpy/PIL.

Physics-IQ case 0001 (2026-11-26, `runs_gemini/physics_iq/.../case_0001_attempt_01`):
status complete, validation **pass** (120 frames, exact tensor, frame 0 exact, no outside
reads/changes, no network commands), 45 tool calls, 11 image views, 36 forwarded model calls
all `gemini-4-neon-high` → `modelVersion gemini-4-neon`, `thinkingLevel HIGH`; 47 auxiliary
calls refused; reported output 79,967 tokens (31,852 thinking) = agy's ACP-reported
`outputTokens`; no token string or account e-mail anywhere in the run dir.

## 4. Differences from the Claude Code route (owner must know)

1. **Interface**: agy ACP, not print mode. `agy -p --input-format stream-json` accepts only
   text blocks (`stream input content block type "image" is not supported`), so print mode
   cannot attach the first frame. ACP has no `init` event: the tool list is verified from the
   actual request tool declarations instead.
2. **Credential inside the sandbox**: agy keeps and refreshes its own OAuth login, so (unlike
   the Claude route's placeholder + injecting relay) the sandboxed client holds a real
   per-attempt copy of the token file. Mitigations: hidden from agent shell commands by agy's
   terminal sandbox; the relay refuses (and does not record) any model request containing the
   access/refresh token; the ACP log is scrubbed of the token strings; copy deleted afterwards.
   agy's `read_file`/`write_file` tools run in the agy process (not in the terminal sandbox) and
   **agy's deny rules (`read_file(/home/agent)` etc.) were NOT enforced under
   `--dangerously-skip-permissions`** in probes: the file tools can read/write the private HOME
   (settings and agent file are ro-mounted). The leak guard is what keeps the token from the
   model.
3. **Permission mode**: `--dangerously-skip-permissions` (equivalent of `bypassPermissions`).
   Required: in every other ACP mode agy's terminal sandbox mounts `/workspace` read-only for
   commands and hides `/opt/runtime`.
4. **System prompt**: custom agent (default identity text + agy sections) instead of agy's
   default agent prompt (which advertises subagents/skills/web search).
5. **Auxiliary models refused**: title generation and agy's tool-call safety rater never run.
6. **Usage counting**: output = candidates + thoughts per response (thinking included, like
   Claude's `output_tokens`). Do not add `thinking_tokens` to agy's `output_tokens`: they are
   already included (`total = input + output` in agy's own usage).
7. **Shell timeout / background**: agy's `run_command` backgrounds commands after
   `WaitMsBeforeAsync` (≤10 s) and the model polls via `manage_task`; there is no 10-minute
   Bash cap as in the Claude route. Backgrounded commands are projected with `exit_code: null`.
8. **HOME writable**: agent shell commands can write the private HOME (outside the run dir,
   discarded), like the Claude route's private HOME.
9. Telemetry, experiments and avatar fetches are blocked by the egress allowlist.
10. **First frame re-encoded**: agy forwards the attached 1280x720 PNG as a 1280x720 JPEG
    (`inlineData image/jpeg`, ~47 KB for case 0001). Same resolution, lossy encoding.
11. **agy scratch dir**: agy's system prompt advertises an artifact/scratch directory under
    `~/.gemini/antigravity-cli/brain/<conversation>/` (in the private HOME). The model may
    write diagnostics there; they are discarded with HOME (images it viewed are kept in
    `logs/tool_images/`). These paths show up in `sandbox_waived_command_paths`.

## 5. Owner decisions applied on this route (2026-11-26)

* Same rules as the Claude route: one real attempt per case (`max_attempts = 1`), no retries
  except recorded infrastructure failures; the static command-path audit is recorded but not
  enforced (`PIXELWORLD_SANDBOX_ENFORCED=1`); private `/tmp` counts as case scratch
  (`PIXELWORLD_PRIVATE_TMP=1`).
* **agy's private scratch dir counts as case scratch too**: writes under
  `/home/agent/.gemini/antigravity-cli/brain/*` (private, per-attempt HOME, deleted afterwards)
  are not outside-workspace changes (`audit._inside`). PCVE 0000 was first failed for this
  only and was re-audited (`run_manifest.original.json` kept, `reaudit` block in the manifest).
* **Scope: demos only.** Because of the quota (section 6) Gemini is run like Fable: three
  official cases per benchmark (Physics-IQ has four: 0001, 0002, 0003, 0005), not the full
  537-case matrix.

## 6. Quota and cost (measured)

Read the quota from agy's interactive `/usage` screen (there is no non-interactive command);
`tools/claude_code/agy_usage.sh`-style scraping: start `agy` in a 260-column tmux pane, trust
a temp dir, type `/usage`, read the two percentages. **Always record it before and after a run.**

* All Gemini 4 models in Antigravity share one **weekly** and one **5-hour** limit, charged in
  proportion to token cost. Plan observed: Google AI Ultra.
* Physics-IQ 0005, `gemini-4-neon-high`, 20.7 min, 120 requests: weekly 89.25% → 86.42%
  (**2.83% of the week per case**), 5-hour 93.01% → 85.66% (7.35%). Tokens: input 2.70M
  (2.52M cached), output 108K (45K thinking). At third-party Neon list prices
  ($3 / $15 / cached $0.15 per MTok) that is about $2.5 per case, i.e. the Ultra weekly
  Antigravity quota is worth roughly $90 of API usage (estimate; Google publishes no numbers).
* Batch of five demos in parallel: weekly 86.42% → ~79% (≈2% per case on average), 5-hour
  83.14% → 52.92%. **The 5-hour limit binds first**: about 13 cases per window; more
  concurrency does not help.
* Implication: one Ultra account covers ~25–35 cases per week. The full matrix (537 cases)
  would need ~15 account-weeks; options were a subset, Hydrogen (≈ half the price), several
  accounts, or the paid Gemini API (≈ $1.2k at Neon prices).

## 7. Login loss and the one-at-a-time rule

On 2026-11-26 ~17:06–17:10 UTC the host agy lost its login (token file and keyring entry
gone; host logs then report `You are not logged into Antigravity`). Most likely cause: each
sandboxed attempt refreshes its **own copy** of the OAuth login; Google rotates the refresh
token on refresh, so parallel copies revoked each other and finally the host's.

Fix and rule:

* `antigravity_harness.write_back_login()` copies the attempt's refreshed token file back to
  the host state after every attempt, only if its expiry is newer.
* **Run Gemini cases strictly one at a time** (no parallel `run-case`, matrix concurrency 1).
  Parallel attempts still race on the refresh token even with write-back.
* Re-login (no SSH needed): run agy in a detached tmux pane with `SSH_CONNECTION` set and
  `DISPLAY` unset so it prints the Google OAuth URL; the account owner opens it, signs in,
  and sends back the code; paste it into the pane. Decline interaction-data collection and
  do not trust non-case folders.

## 8. Status and how to resume

* Done (all validation pass, on HF): Physics-IQ 0001, 0002, 0003, 0005; RBench 0001, 0002;
  PhyGenBench 001, 002; PCVE 0000, 0001.
* Remaining demos: RBench 0003, PhyGenBench 003, PCVE 0002. After re-login run them one after
  another:

```bash
for x in rbench:0003 phygenbench:phygen_003 pcve:pcve_0002; do
  pixelworld run-case --config $PIXELWORLD_HOME/configs_gemini/${x%:*}_smoke5.json \
      --model gemini-4-neon-high --track vision-agent --case ${x#*:}
done
```

* HF publishing: `runs_gemini` and model `gemini-4-neon-high` are in the publisher's `RUNS` /
  `MODELS`. Cases launched with `run-case` (not `matrix run`) leave no `status.json`; write one
  per benchmark (`pixelworld matrix status --config .../<bench>_smoke5.json > runs_gemini/<bench>/status.json`)
  so `sync_sources.py` registers the suites (`<suite>_gemini-4-neon-high`).
