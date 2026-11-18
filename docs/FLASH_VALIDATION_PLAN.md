# PixelWorld-Flash validation

No Flash version is accepted. Experiments below are development interventions
on the same RGB generation task. They do not change the main harness.

## Fixed comparison contract

The functioning-image development cohort contains 20 distinct Physics-IQ
scenarios with balanced camera views. Baseline and candidate share the model,
original prompt, first frame, workspace boundary, 720p/24 FPS/120-frame output,
1M output-token allowance, original RGB writer, and official evaluator pins.
Codex 0.169.0 uses its verified same-version filesystem helper. A tool call is
counted as a visual observation only when an image was actually returned.

Generation and scoring resume saved records. Completed videos are never
regenerated to repair publication or evaluator infrastructure.

## Current evidence and design decisions

| Intervention | Matched evidence | Decision |
|---|---|---|
| Compact save API | Smaller interface and fewer template reads, but cumulative tokens and cost increase | Do not select on interface size alone |
| API with write receipt | Meets the proposed development quality interval; no standalone cost reduction | Keep as quality evidence, not an accepted efficiency gain |
| Four-call/one-candidate guidance | Large token savings accompanied by a substantial native quality decrease | Reject as a quality-preserving setting |
| Exact initial task pack | Full 20-case generation closed; 19 protocol deliveries; no cumulative cost advantage | Do not promote its promising single-case smoke |
| Four core tool schemas | Full 20-case generation closed; 19 protocol deliveries; smaller schemas did not reduce measured total cost | Keep as a negative mechanism test |
| Original calls with archived old returns | Full 20-case generation closed; 19 protocol deliveries; no cost advantage | Keep frozen; correct the receipt-size regression separately |
| Shrinking-only old returns | The corrected one-case smoke passed before expansion to the same 20 development cases | Generation and native scoring are running; no preservation claim |

Live numerical results, failed-attempt spending, example videos, and complete
sanitized traces are on [the Flash dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/flash.html).
Quality JSON remains outside the code repository.

## Trace evidence motivating further work

The baseline has 58 snapshot records but 42 distinct RGB candidates. A snapshot
may record the same RGB output before and after player/inspection completion.
Keep every original record and code revision; merge duplicate previews only for
display. Candidate counts use canonical RGB identity rather than snapshot count.

Native first-deliverable versus final scoring is complete for the 20 baseline
cases. Source tensor/code hashes, frame identity, output contract and final score
identity are checked. The mean score change is small, but some individual changes
are large and the paired interval does not establish the proposed loss margin.
This describes the existing autonomous behavior; it does not prove that an
imposed early stop would preserve quality.

`tools/analyze_refinement_usage.py` measures request usage reported after the
first deliverable's saved timestamp. Deduplicated request totals must exactly
match terminal input/output usage. This interval includes checking, corrections
and validation, with completion timestamps rather than request-start accounting.
It must not be presented as pure revision cost or entirely avoidable spending.

An exact image-return identity audit found only 3 duplicate returns among 147
actual returns, in 2 of 20 baseline cases. Most visual returns are different
crops or frames. Exact deduplication alone is therefore not a supported major
cost reduction. Archive raw evidence; do not infer token savings from bytes.

The retained-call replay audit found negative serialized-byte savings in 61 of
109 requests: 37 of 40 archived returns were shorter than 300 bytes. The correction
replaces a return only when its receipt is shorter. It keeps executable calls,
source/task discovery, input images and recent inspections, with full originals
retrievable inside that case. This guard changes no revision or observation cap.
The original replay arm and the correction remain separate recorded conditions.

## Scoring and provenance

Primary development comparisons use all 120 native 24 FPS frames with matching
24 FPS official reference videos and unique per-case/view masks. The canonical
4 FPS/20-frame decimated development scores remain a separate checkpoint and
selector. Neither subset metric is a full 198-input Physics-IQ Verified score.

The earlier shared-mask scoring error invalidated its quality claims. Those
claims remain withdrawn. The earlier Luna runtime also failed generated-image
returns due to a mismatched filesystem helper; its trajectories are retained
as a different runtime condition, never pooled with functioning Vision runs.

The scoring worker's allocation is verified independently. Memory sizing reads
actual cgroup limits rather than host RAM. Interrupted derived masks are repaired
with the official function; agent outputs and evaluator pins remain unchanged.
Full benchmark publication requires the expected scenario/view coverage and
finite official metrics. Public process status comes from fresh remote probes.

## Economics

Report measured input/output/cached usage and its coverage. Separate RGB delivery,
full protocol acceptance and infrastructure interruptions. A saved video with a
workspace-policy violation stays a protocol failure, with its trace and spending.
Do not assign missing deliveries invented quality scores.

Paired cost uses the exact same closed cases in both arms and includes
unsuccessful-attempt spending. Unknown prior-attempt usage prevents a complete
cost-per-delivery claim. Running attempts are not closed outcomes.

Show measured-cache cost separately from the user's unified 70% cache scenario.
The report declares the reference tariff; free-model estimates are not bills.
Serialized byte reductions and smaller schemas are mechanism measurements,
not provider token savings or evidence of quality preservation.

## Acceptance and next experiment gate

Use scenario-level paired intervals. A single smoke has no sampling interval;
an interval containing zero does not prove equal quality. The proposed
relative 5% loss margin is a development criterion, not benchmark policy.
At least 20 matched scenario groups are required for the development interval
check. Quality, delivery coverage and failed-inclusive costs must all be examined.

Independent confirmation is preregistered on 20 fresh unique scenarios with
IDs at least 103 and cycling views, with no outcome-based case selection.
It compares fresh baseline/candidate runs on GPT-5.6-Luna and GPT-5.6-Sol after
one candidate is selected. It has not launched and supports no current claim.
If corrected replay is unsuccessful, use the measured late-interaction evidence
to preregister one controlled process/tool intervention. Do not stack all prior
changes or repeat a hard cap without isolating its quality effect.

## Publication

GitHub contains code, frontend and small progress metadata. HF contains video,
full sanitized trace and numerical analysis. Incremental publication alternates
accepted-video priority with recent attempt history. Media, detail and full trace
are committed atomically before the readiness flag is set.

Dataset publishers share a cloud-stored commit budget. Commit reservations are
spaced at least 45 seconds apart across video, progress and analysis services,
below the observed Hub limit of 128 repository commits/hour. A 429 creates a
shared cooldown from the server's retry window; it delays publication without
changing generation attempts or marking staged files uploaded. GitHub frontend
publishing runs independently. Larger atomic batches amortize commit overhead.

The live report publishes paired quality/cost, native versus decimated sampling,
first/final candidate evidence and usage coverage. No final Flash guidance is
installed in the main generation path until its acceptance checks pass.
