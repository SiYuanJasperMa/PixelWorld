# PixelWorld-Flash validation

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
