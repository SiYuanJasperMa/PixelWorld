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
