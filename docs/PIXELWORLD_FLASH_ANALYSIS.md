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
| Can task discovery be batched? | Exact prompt/rules/spec text is sent initially; the full 20-case comparison has 19 accepted deliveries and higher failed-inclusive cost | Single-case savings did not generalize to this development cohort | Does not rule out every task-delivery design |
| Are generic tool schemas overhead? | Four general workflow tools occupy 4572 of 8142 serialized schema bytes; the 20-case core-tool comparison has higher cumulative cost | Schema reduction alone did not improve the measured trajectory | Actual request counts also increase; no universal tool-schema conclusion |
| Can older results be archived? | The 20-case retained-call comparison has no cost advantage; many archive receipts are larger than their original returns | Keep the negative result; test a shrinking-only guard separately | Corrected replay is still running and has no acceptance claim |
| Does exact image deduplication address the main overhead? | Only 3 of 147 real image returns are exact duplicates, across 2/20 baseline cases | Exact deduplication alone has limited coverage | Different crops/frames cannot be classified as redundant from byte identity |
| Is computation after first delivery valuable? | Native first/final means are close, with large individual differences; request usage after the first saved delivery is directly measured | Test late interactions separately from pre-generation computation | Descriptive completion-time accounting does not prove all such spending avoidable |

Count exec calls, custom apply_patch calls and actual image returns separately.
Issued image calls do not establish delivered image observations. Cumulative
provider input includes repeated conversation context; serialized byte size is
only a mechanism measurement.

First/final candidate results are published only when final tensor hashes and
metrics match the current native checkpoint. Post-delivery request totals must
agree with terminal usage. The public table exposes both the paired score
interval and the fraction of tokens reported after the first saved delivery;
that interval includes checking, corrections and validation. It is not a causal
early-stopping comparison or proof that refinement can be removed safely.

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

## Verified submission boundary (interim)

The technical submission tool alone is rejected: its full20-case development
comparison has19 protocol deliveries, a3.3% lower mean v1 and a8.3% higher
failed-inclusive cost per accepted video. Four completed traces show the cause:
after the receipt is accepted, the agent still issues exactly one further
request that returns only a short message and changes no pixels.

The boundary variant keeps the task, tools, history and autonomous revision,
but declines that next request before upstream sampling once a case-bound
receipt verifies the current RGB/code hashes, the original first frame and the
execution policy. Two completed development cases terminate with
`verified_submission_boundary`; both record complete usage from finished gateway
responses cross-checked against native Codex counters.

| Case | Boundary input/output | Baseline input/output | Boundary v1 | Baseline v1 |
|---|---|---|---|---|
| 0056 | 92,961 / 9,933 | 821,485 / 24,980 | 0.3308 | 0.3108 |
| 0047 | 123,488 / 9,481 | 270,866 / 15,003 | 0.1562 | 0.1011 |

Mean input falls71.5% and mean v1 rises0.0376 on these two pairs. Both baselines
were high-overhead cases and n=2 cannot estimate population effects, so this is
interim mechanism evidence only. The remaining development cases are running;
no Flash version is accepted and no preservation claim is made. Raw evidence is
in `runs/flash_submit_boundary_probe_20261118/analysis/interim_boundary_pairs.json`.
