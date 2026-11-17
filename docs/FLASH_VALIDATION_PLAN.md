# PixelWorld-Flash validation

> Quality revalidation: the previous subset adapter selected generated masks
> from a shared directory. Its quality figures are unverified and must not
> support preservation claims. All arms are being rescored with unique
> per-case mask directories (`per-case-v2`); token measurements are unaffected.


The baseline cohort remains fixed. Flash is an analysis variant of the same
RGB task, output contract, input, model and evaluator. It is accepted only
after measuring both token usage and Physics-IQ quality.

## Evidence currently available

The existing Luna development subset contains 20 matched Physics-IQ cases.
The input-summary and run/check helper bundle did not reduce measured cost
against the baseline. This supports testing a different tool interface, rather
than adding more helper descriptions to the prompt.

The five-line guidance bundle without the scratch-path line used approximately
82k input tokens and 9.1k output tokens per fully measured attempt. The exact
five-line-plus-scratch candidate has now generated 20 valid RGB artifacts and
uses 92.9k input and 9.8k output tokens. These are distinct arms. Its official
quality result is pending; the earlier arm's score must not be assigned to it.

The strict four-command/one-candidate arm is still running. Interim means must
include the number of closed attempts and must not treat running attempts as
successes or failures. A one-shot arm is a stress test, not an accepted setting.

## Questions for the trace audit

| Design question | Required evidence | Current limitation |
|---|---|---|
| Can commands be batched? | Per-case command counts, provider input usage per model call, stages before/after first generation | A lower command count alone does not show that every removed call was unnecessary |
| Should tool replies be compact? | Full returned text size and its repetition across later model requests; preserved external logs | Byte counts are not provider token counts |
| Is repeated reading a major cause? | Reads of the same unchanged file, including Python reads | The existing shell-only detector found few repeated reads; this clause has not been isolated |
| Can inspection/revision caps preserve quality? | First/final candidate comparisons and matched evaluator results | Luna's observed visual-return count is low; the current bundle does not isolate an inspection effect |
| Can tools reduce handoff overhead? | A controlled interface change that reduces model-facing output while preserving full external records and an escape path to inspect details | The previously tested helper-file bundle increased cost |

## Next controlled tests

1. Finish the current 20-case exact-guidance and aggressive arms and score their
   saved RGB videos with the pinned official evaluator. Resume completed
   evaluation records rather than generating replacement videos.
2. Extract actual per-call input/output/cached usage from the existing rollout.
   Measure the relation between calls, growing history and token consumption;
   clearly label any replay-size estimate as an estimate.
3. Test compact tool results and a combined write/run/validation interface
   separately from stricter prompt caps. Preserve full tool outputs externally
   and allow the agent to retrieve an unabridged result when needed. No helper
   may synthesize or continue pixels.
4. Use new held-out cases for confirmation, with a second roster model. The
   existing 20 cases are development data because they have already informed
   several designs.

## Reporting and acceptance

Report valid RGB delivery, full protocol validity, infrastructure interruptions,
usage completeness and sample counts separately. A blocked scratch write is
not evidence of a bad video, and an infrastructure-interrupted attempt with a
saved video is not a fully measured cost observation.

Report measured input/output/cached tokens. Show measured-cache cost separately
from the user's normalized 70% cache scenario. Pricing is a stated reference
tariff, not a charge for free test models. Include failed-attempt expenditure
when reporting cost per delivered video.

Report matched Physics-IQ differences with intervals. An interval containing
zero does not prove equal quality. Before confirmation, use a relative 5%
quality-loss margin as the proposed tolerance for a small decrease and examine
the entire paired interval against that margin. This margin is a new validation
choice, not a property of the official benchmark. If evidence cannot support
the margin, describe the uncertainty and collect additional held-out cases.

The final publication should expose one accepted Flash version. Experimental
arms and dropped designs remain documented for attribution and reproducibility.


## Repaired-runtime confirmation

The development traces contained real `view_image` attempts but no returned
images: worker staging had supplied a different `bwrap` from the one expected
by Codex 0.169.0. The bundled helper is now verified, positive and negative
folder-access tests pass, and actual image returns are captured. Existing
development results are retained as a separate runtime condition.

Confirmation compares baseline, compact output API, API with a write receipt,
and strict prompt guidance on 20 previously unused scenarios with balanced
camera views. All share the same model, input, resolution, frame rate, token
budget, functional visual tools and factual scratch-directory instruction.
The compact API retains the exact original output writer in an inspectable
workspace subdirectory; it generates no pixels. The receipt reports completed
writing and does not certify final harness acceptance.

[Live Flash monitoring](https://huggingface.co/spaces/HuggingFriends/PixelWorld-dashboard)
provides progress, token measurements and matched subset quality. Score data
is published on HF and kept outside the GitHub code repository.


## Replay study (offline only)

The current replay prototype preserves the original task and input images,
keeps recent complete tool interactions, and can archive older tool text or
known generated inspection images. Full originals remain retrievable in the
same case workspace. This is not enabled in the baseline or confirmation
cohort. Serialized byte reductions are an offline mechanism measurement, not
provider token savings or evidence of quality preservation. A matched native
run and official scoring are required before selecting this design.


The replay smoke uses a separate authenticated loopback relay and an explicit
case-to-workspace registry. It forwards to the same model gateway, retains all
task/input messages and recent complete tool interactions, and stores replaced
call/result pairs inside that case's `.tmp/harness-history/`. Older known
generated inspection images can be restored from their archived image files.
The original baseline gateway is unchanged. The smoke must pass artifact,
streaming and official quality checks before any replay matrix is launched.


## Functioning-image comparison and current selection decision

The 20-scenario baseline, compact API, receipt, and four-call arms have completed
generation and per-case-v2 scoring. The compact API has no measured cumulative
token advantage; the aggressive guidance lowers cost but does not support
quality preservation. No final Flash version is accepted. The live report on HF
contains the paired means and scenario-bootstrap intervals, without putting raw
scores in the code repository.

The exact-task-pack arm includes the unchanged prompt, rules and task-spec text
in the initial request. It changes task delivery, without a generated scene
summary, scaffolding, inspection requirement or revision cap. Its one-case smoke
is insufficient for sampling uncertainty; the remaining matched cases are
running sequentially after the earlier matrix. Selection requires completing
this matrix, investigating actual tool trajectories, then new held-out cases and
cross-model validation.

A one-scenario smoke has no bootstrap confidence interval and cannot satisfy
the quality-preservation criterion. Twenty matched scenario groups are the
minimum for reporting the planned development interval check.


## Native temporal scoring and transport probes

The earlier functioning-image comparison uses the canonical4FPS development
adapter: generated120-frame/24FPS videos are decimated to20 frames, with
matching official4FPS reference videos and masks. These are valid development
measurements, distinct from official scoring at the native submission FPS.
The final720p/24FPS quality-preservation decision additionally requires native
24FPS/120-frame matched scores. `--native-scoring` retains a separate checkpoint
and uses the same pinned `process_view`, all120 frames, and matching official
24FPS references/masks. No model video is regenerated. Both sampling conditions
are explicit in the live report and selector.

Two further one-case mechanism probes retain the baseline task and output
contract. One keeps every original tool call, including executable patch text,
and replaces only older eligible tool returns with retrievable archives. Task
discovery, source images, and recent generated inspections remain intact. The
other keeps the full history and exposes only exec, write_stdin, apply_patch,
and view_image; general user-input/goal tool definitions are omitted. Actual
request logs showed8142 bytes of tool schemas, of which4572 bytes belonged to
those four general workflow tools. Schema byte reduction is not yet provider
token savings or quality evidence. These probes are exploratory single cases,
selected for high observed input overhead, and are not held-out acceptance.

Trace counts now include custom apply_patch calls as well as ordinary function
calls. Report shell execution, patches and actual image returns separately.
Future `python -m` launches use the frozen snapshot as cwd as well as PYTHONPATH;
cwd can otherwise shadow the intended package. The active probe package bytes
were checked against its snapshot before subsequent changes.


Native24 backfill is now complete for the baseline, compact API, receipt and
aggressive-guidance arms. No Flash version is accepted: the receipt meets the
proposed development quality interval but has no standalone cost reduction;
the aggressive arm saves tokens without meeting the quality-loss criterion.
Both transport smoke cases pass RGB/protocol/native24 scoring and have expanded
to the same20 development scenes. Their single-case comparison is exploratory;
the two interventions remain separate until matched results support selection.

The scoring worker has a verified100-core/900GiB allocation. The devbox's actual
cgroup limit was32GiB, so using physical host memory to select parallel score
workers was unsuitable. Evaluation now checks exposed container limits, stages
needed evaluator dependencies on cloud storage, and uses worker process probes
for live status. Interrupted derived masks are checked and repaired with the
original mask function, without regenerating agent video outputs. Full-score
publication requires official scenario coverage and finite metrics for all three
views.


## Matched economics and corrective replay

Interim economics compare the exact same closed, fully measured cases. They do
not compare a partial expensive subset against the baseline's full20-case mean.
Unsuccessful-attempt expenditure is included in accepted-video cost; a case with
unknown prior attempt usage is excluded from complete paired-cost claims.

A trace audit of the retained-call replay found61 of109 requests with negative
serialized byte savings.37 of40 archived returns were shorter than300 bytes.
The corrective smoke therefore adds `--only-shrinking-returns`: preserve short
returns when the archive receipt is not smaller. It changes no task, image,
code-call identity, observation count or revision limit. The current matched20
replay remains frozen; the correction is a separate one-case mechanism test.

Independent confirmation is preregistered on20 unique scenarios with IDs>=103,
cycling camera views and using no outcome-based selection. It will compare fresh
baseline/candidate runs on GPT-5.6-Luna and GPT-5.6-Sol after a candidate is
selected. This confirmation has not launched and cannot support current quality
claims.
