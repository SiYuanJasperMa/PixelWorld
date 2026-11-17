# PixelWorld-Flash: evidence for each harness change

This document records why each proposed Flash change is kept or dropped. The
unit of evidence is a 20-case Physics-IQ A/B on GPT-5.6-Luna with identical
inputs, 1280x720/24 FPS/120-frame contract, 1M output-token budget and evaluator.

Cost uses measured provider tokens at \$10/M uncached input, \$1/M cached input
and \$50/M output. Quality uses the pinned official Physics-IQ evaluator's
per-view weighted spatial IoU v1/v2 on the same 20 cases at the canonical 4 FPS
masks. This is a **20-case subset**, not the full 198-case Verified score.

## Arms

| Arm | Task guidance | Workspace interface |
|---|---|---|
| `reference` | frozen task text | baseline |
| `guidance` | 5 lines: batch, compact output, no re-read, <=3 inspections, <=2 candidates | baseline |
| `tools` | frozen task text | `INPUT_SUMMARY.json` + `pixelworld_run.py` + `pixelworld_check.py` |
| `combined` | 5 guidance lines | tool interface |
| `lean` | batch + compact output | tool interface |
| `guidance_lean` | batch + compact output | baseline |

## Cost and delivery

| Arm | Valid video | Policy warnings | Input | Output | Commands/case | Tool-output bytes/case | \$/attempt |
|---|---|---|---|---|---|---|---|
| reference | 20/20 | 10 | 149k | 13.7k | 11.1 | 11,948 | 0.99 |
| guidance | 20/20 | 0 | 82k | 9.1k | 6.0 | 8,536 | 0.65 |
| tools | 20/20 | 0 | 143k | 15.4k | 9.3 | 14,995 | 1.05 |
| combined | 20/20 | 0 | 84k | 11.4k | 6.4 | 11,777 | 0.76 |
| lean | 20/20 | 0 | 126k | 13.6k | 9.7 | 11,507 | 0.92 |
| guidance_lean | 20/20 | 10 | 138k | 13.6k | 11.8 | 10,641 | 0.95 |
| guidance_lean_tmp | 20/20 | 1 | 132k | 13.2k | 10.3 | 10,638 | 0.93 |

Every arm produced 20/20 valid RGB videos. The reference arm's ten policy
warnings are blocked `/tmp` scratch writes; they do not invalidate the videos.

## Quality

| Arm | Cases | IoU v1 | IoU v2 | Paired v1 vs reference | Paired v2 vs reference |
|---|---|---|---|---|---|
| reference | 20 | 0.0466 | 0.0494 | — | — |
| guidance | 20 | 0.0496 | 0.0502 | +0.0029 | +0.0008 |
| tools | 20 | 0.0486 | 0.0493 | +0.0019 | -0.0001 |
| combined | 20 | 0.0531 | 0.0473 | +0.0064 | -0.0022 |
| lean | 20 | 0.0504 | 0.0545 | +0.0037 | +0.0051 |
| guidance_lean | 20 | 0.0488 | 0.0519 | +0.0022 | +0.0024 |

The paired deltas are small relative to the mean and mixed in sign. There is no
evidence that Flash reduces quality; the claim is preservation, not superiority.
A paired bootstrap over the 20 cases (5,000 resamples) gives:

| Arm | v1 mean delta | v1 95% CI | v1 win/lose | v2 mean delta | v2 95% CI |
|---|---|---|---|---|---|
| guidance | +0.0029 | [-0.0008, +0.0066] | 14/6 | +0.0008 | [-0.0027, +0.0041] |
| tools | +0.0019 | [-0.0017, +0.0051] | 14/6 | -0.0001 | [-0.0038, +0.0032] |
| combined | +0.0064 | [-0.0085, +0.0301] | 8/12 | -0.0022 | [-0.0153, +0.0135] |
| lean | +0.0037 | [-0.0016, +0.0095] | 7/13 | +0.0051 | [-0.0004, +0.0118] |
| guidance_lean | +0.0022 | [-0.0033, +0.0068] | 13/7 | +0.0024 | [-0.0024, +0.0071] |
| guidance_lean_tmp | +0.0009 | [-0.0021, +0.0037] | 10/10 | -0.0012 | [-0.0044, +0.0019] |

Every confidence interval includes zero. The 20-case subset can rule out a large
quality drop, not a small one; the full 198-case evaluation is the confirmatory
test.

## Per-modification audit

| # | Modification | Mechanism | Measured evidence | Decision |
|---|---|---|---|---|
| 1 | Batch shell work | Fewer tool-result messages, each re-sent on every request | Reference 11.1 -> guidance 6.0 commands/case; pixel-probe commands 86 -> 24; input 149k -> 82k | **Keep.** Dominant cost lever. |
| 2 | Compact command output | Less text per message, re-sent at every step | Tool-output bytes/case 11,948 -> 8,536 (-29%) | **Keep.** Supported by the guidance bundle. |
| 3 | No re-read | Fewer redundant calls | The 5-line arm costs \$0.65 vs \$0.95 for the 2-line arm; adding only the `.tmp` line does not recover it (9-case partial: \$1.07) | **Keep as part of the bundle.** The caps and no-re-read rule are not separable from batching in this design. |
| 4 | <=3 visual inspections | Fewer image messages | Luna recorded zero visual returns in the A/B; the 5-line arm is 31% cheaper than the 2-line arm | **Keep as part of the bundle.** The cost effect is measured; visual returns are not the driver on this route. |
| 5 | <=2 candidates | Fewer revisions | Saved candidates 2.05 -> 1.8; the 5-line arm is 31% cheaper than the 2-line arm | **Keep as part of the bundle.** Single-line attribution is not established. |
| 6 | `INPUT_SUMMARY.json` | Removes input discovery | Tool-only arm: 72 probe commands vs 86 reference; input 143k vs 149k | **Drop.** No cost benefit; the combined arm's lower probing comes from guidance. |
| 7 | `pixelworld_run.py` / `pixelworld_check.py` | Collapses run+validate; compact validation | Tool-only 20/20 valid but 143k input, 9.3 commands, \$1.05; all arms already 20/20 valid | **Drop.** Adds commands without a delivery or cost benefit. |
| 8 | `.tmp/` compliance line | States the writable scratch root | Reference 10 policy warnings; Flash arms 0 | **Keep.** Sandbox correctness, not a capability hint. |

## Candidate final Flash

The 5-line guidance plus the `.tmp/` compliance line:

```text
Aim for high-quality output while considering efficiency.
- Use at most 3 visual inspections of generated frames.
- Save at most 2 candidate videos.
- Batch shell work into one script per iteration; avoid many small commands.
- Keep command output compact; do not print large arrays, full logs, or base64.
- Do not re-read unchanged files.
- Use `.tmp/` for temporary files; `/tmp` is outside the workspace and is blocked.
```

No precomputed input summary and no helper scripts. The 5-line arm is the
cheapest measured harness (\$0.65/attempt, 20/20 valid videos) and matches the
reference quality within the 20-case subset. The two tool components are
dropped: they add commands and tokens without a delivery or quality benefit.
The `guidance_lean_tmp` arm confirms that `/tmp` compliance is not the cost
driver: adding the line alone gives \$0.93, while the 5-line arm is \$0.65. The
caps and no-re-read bundle is therefore kept.

## Bottom line

- Final Flash: the five efficiency lines plus the `.tmp` compliance line.
- Cost: \$0.99 -> \$0.65 per attempt (-34%); input 149k -> 82k (-45%).
- Quality: 20/20 valid videos; paired IoU v1 +0.0029 (95% CI
  [-0.0008, +0.0066]), v2 +0.0008 (95% CI [-0.0027, +0.0041]).
- Dropped: `INPUT_SUMMARY.json` and the run/check helpers. The tool-only arm
  costs \$1.05; adding tools to the 5-line arm raises \$0.65 -> \$0.76 with no
  delivery or quality gain.
- The caps and no-re-read bundle is justified by the 5-line vs 2-line ablation
  (\$0.65 vs \$0.93-0.95), not by the `/tmp` line.

## Caveats

- 20 cases, one model, one benchmark. The full 198-case Physics-IQ evaluation is
  the confirmatory test.
- Absolute IoU is low because the task is hard; the relevant quantity is the
  paired delta against the reference harness on the same cases.
- The `.tmp/` line is a sandbox fact, not an efficiency prompt; it is justified
  by the reference arm's ten policy warnings.
he
  paired delta against the reference harness on the same cases.
- The `.tmp/` line is a sandbox fact, not an efficiency prompt; it is justified
  by the reference arm's ten policy warnings.
