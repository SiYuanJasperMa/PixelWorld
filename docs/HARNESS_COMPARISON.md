# Coding-agent harness comparison

This analysis asks whether PixelWorld's results depend on the coding-agent
harness. It changes the agent software while retaining the main experiment's
720p, 24 FPS generation contract. It is independent of the resolution, FPS,
thinking-level, and PixelWorld-Flash studies.

## Frozen software

| Harness | Version | Source revision | Role |
| --- | --- | --- | --- |
| Codex | 0.169.0 | Existing frozen PixelWorld runtime | Reuse the validated main Vision deliveries |
| OpenCode | 1.19.6 | `f4e363c8295fa90a03dddc451882fffe8fbce357` | Additional agent harness |
| Claude Code | Pending admission | Proprietary binary; executable digest required | Additional black-box harness |

The active comparison is Codex, OpenCode, and Claude Code. OpenCode is
MIT-licensed and exposes headless JSON events. Claude Code is proprietary and
is evaluated as a black box, not presented as a source-reproducible harness.
Lock its executable version and checksum before any benchmark dispatch. Pi
and DeepSeek Harness are outside this comparison; retain the Pi artifacts as
historical integration evidence.

Sources:

- [OpenCode release source](https://github.com/anomalyco/opencode/tree/v1.19.6)
- [OpenCode license](https://github.com/anomalyco/opencode/blob/v1.19.6/LICENSE)
- [Claude Code license](https://github.com/anthropics/claude-code/blob/main/LICENSE.md)
- [Claude Code CLI documentation](https://code.claude.com/docs/en/cli-reference)
- [Historical Pi source](https://github.com/earendil-works/pi/tree/v1.6.0)

## Experiment scope

The initial analysis uses Physics-IQ's full 198-case main Vision cohort and
the five free analysis models: GPT-6.1-Sol, GPT-5.6-Sol, GPT-5.6-Terra,
GPT-5.6-Luna, and DeepSeek-V4.1-Flash. There are 990 existing Codex deliveries
to reuse, and 1,980 additional planned deliveries across the two new harnesses.
This is the registered analysis scope; any wider benchmark scope is
recorded explicitly before its cohorts are registered.

Every row uses the same official prompt and first frame, the same 720p output
size with the supplied aspect ratio, 24 FPS, five seconds, and 120 frames.
The existing task specification is authoritative for each case. There is no
new resolution or FPS ablation. PCVE, if subsequently included, retains its
standard four-second editing contract rather than adopting five seconds.

## Controlled variables

The user task text, generation rules, input hashes, model endpoint and exact
model ID, requested high reasoning effort, 1M reported output-token limit per
real attempt, five-real-attempt policy, and evaluator remain identical.
Infrastructure errors do not spend the model-attempt allowance. Existing
case-specific rescue authorizations do not automatically extend to these new
cohorts. Accepted Codex videos are never regenerated for this comparison.

The selected harness retains its native system prompt, tool schemas,
conversation serialization, context management, planning and revisions.
Those behaviors are the independent variable. Record them rather than
replacing the agent loop with a shared scripted solver. A difference in
performance is attributed to the model-harness combination, not to model
weights alone.

The common capability boundary stays unchanged: local Python/NumPy/PIL RGB
generation, code execution, file edits, and optional visual inspection.
External image/video models, learned renderers, physics engines, other
benchmark cases, reference continuations, and evaluator feedback are not
available to the agent. Harness integration must enforce the same case-only
filesystem and tool-network boundary; a working API call alone is insufficient.

## Integration acceptance

1. Preserve the frozen executable and complete dependency lock; never resolve
   `latest` at experiment launch. Record actual executable version and hashes.
2. Give each attempt separate configuration, data, cache and session
   directories on cloud storage. Never reuse the host's interactive agent
   history, provider credentials or OpenCode/Neeko database.
3. Verify the emitted API request uses the intended exact model and reasoning
   effort. Do not silently substitute a built-in model, provider, or thinking
   mode. Record an incompatibility if the requested setting is unsupported.
4. Verify initial-image attachment and output-image inspection on Vision.
   Verify that filesystem access cannot escape the case through absolute paths,
   parent traversal or symlinks, and tool execution cannot access the network.
5. Preserve raw native events and the complete native session, tool arguments,
   tool results, candidates, code revisions, errors and per-request usage.
   Any normalized dashboard projection accompanies the originals.
6. Test reported input/output/cache accounting, budget enforcement and normal
   API waiting/retry behavior. Missing usage is explicitly unavailable; it is
   never treated as zero.
7. Run one standard-quality Physics-IQ case per harness through the common
   RGB delivery validator and the pinned official evaluator before the full
   cohorts. Technical acceptance must not depend on its physics score.

OpenCode and Claude Code remain preparation entries until their adapters pass
these acceptance checks. In particular, a successful Claude Code API request
does not establish equivalent native model routing, image handling, or case
isolation. Neither may dispatch full cases on the basis of a text probe.
GPT-6.2-Sol is an additional planned model only after the same exact route is
verified across all three harnesses; record missing cells explicitly.

## Preparation evidence and historical Pi pilot

Both frozen executables passed their version checks. A no-tool, non-benchmark
API probe through each native CLI returned `HARNESS_READY` from GPT-5.6-Luna.
Pi emitted native per-request input/output/cache usage. This verifies basic
provider interoperability, not generation, visual inspection, token-limit
cutoff, or case-only isolation.

Pi subsequently passed a standard Physics-IQ Vision video smoke on case
0001 with GPT-5.6-Luna: 1280×720, 24 FPS, 120 RGB frames. The common validator
confirmed exact first-frame preservation and PNG/tensor identity. The trace
contains five actual returned images, 145,878 input tokens, 131,358 reported
cached input tokens, and 13,525 output tokens. These are observed usage,
not the standardized cache assumption used in the cost comparison.
The smoke has not yet established a full-cohort physics score.

[Playable Pi smoke and trace](https://huggingfriends-pixelworld-dashboard.static.hf.space/native_harness_smoke/pi/index.html)

Pi retains its native read/write/edit/bash schemas and agent loop. An
extension delegates their file and shell operations to Bubblewrap with a
case-only writable mount, read-only runtime libraries, an empty tool
environment, no `/proc`, and an unshared network namespace. Probes denied
neighbor-case reads, symlink escapes, process-environment reads, and network
access. Built-in tools are disabled so an extension load failure cannot
fall back to unisolated tools. Raw native events and sessions are retained
alongside the external validation projection. Per-response usage is
deduplicated; cache reads/writes are included once in total input.

OpenCode's startup log showed parent-repository discovery and creation of its
own Git snapshot even when launched inside a fresh child directory. The
adapter must scope project discovery and filesystem access to the case and
keep these snapshots on cloud storage. Running `opencode run` inside a case
folder by itself does not establish the same boundary as the frozen Codex
permission profile.

Recreate the staged Linux x64 packages by copying
`pixelworld/assets/harness_comparison/package.json` and `package-lock.json`
into a dedicated cloud runtime directory, then running `npm ci --ignore-scripts`
there with a cloud npm cache. Invoke Pi with the staged
`node-linux-x64/bin/node`; the host's Node version is not the experiment runtime.
The accompanying `software_lock.json` records the verified source pins and
package distribution integrity.

Set `PIXELWORLD_NATIVE_RUNTIME` to that cloud runtime. The Python execution
environment also needs Bubblewrap at `bin/bwrap`. In an otherwise identical
benchmark config, set each Pi model's `cli_backend` to `pi` and `cli_version`
to `1.6.0`; retain its Responses provider and frozen model catalog so the
original context limit is available. Use the normal `pixelworld run-case`
and `pixelworld run-matrix` entry points. Do not substitute a model or
change the shared task to make a harness smoke pass.

## Reporting

Use one paired table with model rows and harness columns for Physics-IQ
Verified. Report completion rate separately, so quality conditional on delivery
is not confused with the ability to deliver a video.

For each model/harness, also report input/output tokens, official-price cost
under the common 70% cache scenario, elapsed time, model calls, tool calls,
and saved candidates. Preserve reported cache usage; apply long-context
premiums to individual requests. Separate model/API time from infrastructure
waiting. Use the existing accepted-run cost convention for the comparable
table and additionally retain the cost of failed attempts for end-to-end
cost per delivered prompt.

Keep all three harnesses on the same case set for paired comparisons. Publish
the complete 198-case score only when the full accepted scope is available;
partial scores must state their paired coverage.
