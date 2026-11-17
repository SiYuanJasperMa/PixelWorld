# Trace-grounded Flash analysis

No Flash setting is accepted yet. This document describes measured behavior and
its limits; the current numerical quality reports live on HF rather than in the
code repository.

## Evidence conditions

The first Luna development cohort had attached input images but failed generated
image returns because the staged helper did not match Codex0.169.0. Its token
usage describes that runtime condition. Quality numbers from the old shared-mask
adapter were withdrawn and are not reproduced here.

The functioning-image comparison covers20 distinct Physics-IQ scenarios with
balanced views. Baseline, compact API, receipt and aggressive guidance share the
model, task/input identities,720p/24FPS/120-frame output,1M attempt budget and
working image tools. Native24 scoring now covers all four arms. Canonical4FPS
measurements remain a separately labeled development condition.

## Design evidence

| Question | Measured observation | What it supports | Limit |
|---|---|---|---|
| Does a smaller save interface suffice? | Template reads and returned text decrease, but cumulative input and cost do not | Interface size alone is insufficient | Does not isolate every source of additional calls |
| Does a write receipt help? | Native quality meets the proposed development interval while cost does not decrease | Reliable write feedback is a quality candidate | No standalone efficiency advantage or held-out proof |
| Can calls/revisions be capped aggressively? | Tokens fall; matched native quality does not meet the loss criterion | Cost-quality tradeoff, not an accepted setting | Bundle does not identify each removed interaction's value |
| Can task discovery be batched? | Exact prompt/rules/spec contents are sent initially; later outcomes and failures are measured | A process-only intervention without forced revision limits | Single-case success did not establish a global benefit |
| Are generic tool schemas overhead? | Actual requests include8 schemas;4 general user/goal tools occupy4572 of8142 serialized bytes | Test4 pixel-task tools independently, preserving full history | Byte savings are not provider token savings; one focused smoke is exploratory |
| Can older results be archived? | Initial replay lost task-file content and caused repeated reads; corrected version retains task discovery and original calls | Test retrievable returns without losing executable code or source inputs | Full paired quality and cross-model evidence remain required |

Count exec calls, custom apply_patch calls and actual image returns separately.
Issued image calls do not establish delivered image observations. Cumulative
provider input includes repeated conversation context; serialized byte size is
only a mechanism measurement.

## Acceptance and reproducibility

Compare matched cases and bootstrap by scenario. A single smoke has no sampling
interval. A confidence interval containing zero does not prove quality parity.
Twenty matched scenarios are the minimum for the planned development interval
check; final selection also needs new validation cases and another model.

RGB validity and protocol acceptance differ. A video may exist while a workspace
policy violation prevents acceptance; its trace and spend stay visible. Only
accepted outputs enter the corresponding quality comparison, and delivery
coverage remains explicit. No missing output is assigned an invented score.

The measured-cache tariff and normalized70% cache estimate are separate. Include
failed-attempt expenditure in accepted-video cost and withhold complete-cost
claims when usage is incomplete.

Frozen module execution uses the snapshot as cwd as well as PYTHONPATH. Evaluation
checks container limits rather than host RAM, resumes derived artifacts safely,
and validates full official scope before publishing main scores. The worker's
allocation and actual process identities are recorded outside generation cases.

[Live native24 report and example videos](https://huggingfriends-pixelworld-dashboard.static.hf.space/flash.html)
contains current paired results and complete sanitized traces.
