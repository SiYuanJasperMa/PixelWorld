# Gemini through native Antigravity

PixelWorld supports the official Antigravity CLI **1.5.18** through ACP. The
standard model is `gemini-4-neon-high` with High reasoning. Generation uses
shell and file tools to write RGB pixels; no learned renderer or image/video
generation service is enabled.

## Setup and authentication

Install and retain the pinned official binary, then log in interactively with
`agy`. On an SSH host, the native client provides a Google authorization link
and asks for a one-time code. Keep the OAuth state private and outside run
outputs. Never copy real credentials into the case workspace or client HOME.

Set these operator environment variables:

```bash
export PIXELWORLD_CONTROLLED_RUNTIME="$PIXELWORLD_HOME/runtime"
export PIXELWORLD_CONTROLLED_LOCKS="$PIXELWORLD_HOME/locks.json"
export PIXELWORLD_AGY_STATE="$HOME/.gemini/antigravity-cli"
export PIXELWORLD_AGY_PRIVATE_ROOT="$PIXELWORLD_HOME/private/agy-clients"
```

`locks.json` must contain `harnesses.antigravity` with the pinned binary path,
`version: "1.5.18"`, and its SHA-256. The runner verifies the digest. Keep this
operator file outside Git. `HTTPS_PROXY` may specify an HTTP CONNECT proxy for
the external relay. CLI auto-update is disabled.

Add this model to a standard benchmark configuration:

```json
{
  "key": "gemini-4-neon-high",
  "model": "gemini-4-neon-high",
  "reasoning_effort": "high",
  "cli_backend": "antigravity",
  "cli_version": "1.5.18",
  "tracks": ["vision-agent"],
  "provider": {
    "type": "google_subscription",
    "id": "google_subscription",
    "name": "Native Antigravity subscription"
  }
}
```

Use `filesystem_policy: "case-only-v1"`, `harness_variant: "baseline"`, an
explicit output-token budget, and the existing frozen benchmark inputs.
Preserve the protocol and evaluator pins. Run `pixelworld doctor`, the test
suite, and a one-case smoke before a full matrix.

## Tools, images and records

The custom agent exposes `read_file`, `write_file`, `edit_file`, `run_command`,
and Antigravity's required `manage_task` for asynchronous shell commands.
Web search, image generation, browser tools, subagents, plugins and MCP servers
are excluded. The relay verifies the actual model tool declarations.

ACP attaches the first frame as image content. When present, the standard
source/conditioning inspection image is attached too. Print mode cannot attach
images. Antigravity may re-encode attached PNGs as JPEGs; retain native wire
records to distinguish input file hashes from transported image bytes.

Each run preserves native ACP events, tool logs, code revisions, intermediate
candidates, model requests and responses, and terminal token usage. Raw model
exchanges are in `logs/native/cloudcode_wire`. Session bootstrap calls contain
account data, so retain only their size, digest and approved client metadata.
Credential headers are excluded from receipts.

Output usage is `candidatesTokenCount + thoughtsTokenCount`. Native ACP's output
count already includes thinking; do not add the thinking count again. The
per-attempt budget is enforced after reported native usage, with any overshoot
recorded.

## Isolation and concurrent authentication

The client has a per-case filesystem and network namespace. The only host
writable mounts are its workspace and private HOME; `/tmp` is private. System
libraries and the pinned runtime/client are read-only. Host procfs is absent;
the empty `/proc` only supplies a client-executable symlink. Other experiments,
reference directories and the operator's OAuth state are outside all mounts.

The client HOME contains a **non-secret placeholder**. A fixed-upstream relay
outside the sandbox injects the real access token and forwards only approved
Cloud Code methods. Direct Google CONNECT traffic from the client is refused.
The external relay refreshes the canonical login by running the native catalog
command under a shared file lock. Parallel cases share this external login;
they never refresh separate copies.

Antigravity file-tool deny rules are not a filesystem boundary in permission
bypass mode. The earlier collaborator prototype held real credential copies in
client HOME and relied on a leak filter. This adapter replaces that behavior.
Verify both direct file access and shell access on the actual worker before
benchmark use.

## Smoke, quota and full admission

Smoke one standard case per benchmark and record wall time, input, cached
input, output, validation, concurrency and native account quota before/after.
Overlapping cases permit an aggregate quota measurement; they do not establish
an exact quota charge for each individual case.

Use native `/usage` or `retrieveUserQuotaSummary` with the account project
obtained by native `loadCodeAssist`. Omitting this project can return only the
Claude/GPT quota, which is not the Gemini quota. Report both `gemini-weekly` and
`gemini-5h` remaining fractions and reset times. Publish derived quota fields
only, without account identifiers.

Start a full matrix only when valid smoke deliveries and measured remaining
quota support the requested scope, with a stated reserve. Treat a rounded zero
quota change as insufficient evidence for unlimited capacity. A five-hour quota
pause is infrastructure state; retain active records and resume after the quota
reset without calling it model failure.
