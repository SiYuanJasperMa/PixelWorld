# PixelWorld-Flash harness variant

> Quality revalidation: the previous subset adapter selected generated masks
> from a shared directory. Its quality figures are unverified and must not
> support preservation claims. All arms are being rescored with unique
> per-case mask directories (`per-case-v2`); token measurements are unaffected.


PixelWorld-Flash is a **harness variant**, not a benchmark, model or renderer.
The task text, benchmark inputs, RGB output contract, evaluator and token budget
stay fixed. Only the agent's process guidance changes.

The currently tested guidance candidate uses five efficiency lines plus one
factual sandbox line. Further tool and process experiments are underway; see
[the validation plan](FLASH_VALIDATION_PLAN.md). The tool interface described below was tested and dropped from the final
definition. The full component evidence is in
[`PIXELWORLD_FLASH_ANALYSIS.md`](PIXELWORLD_FLASH_ANALYSIS.md).

## Final Flash task text

```text
Aim for high-quality output while considering efficiency.

Cost-control profile:
- Use at most 3 visual inspections of generated frames.
- Save at most 2 candidate videos.
- Batch shell work into one script per iteration; avoid many small commands.
- Keep command output compact; do not print large arrays, full logs, or base64.
- Do not re-read unchanged files.
- Use `.tmp/` for temporary files; `/tmp` is outside the workspace and is blocked.
```

Use `harness_variant: "baseline"` with this task text. No helper files are
installed.

## Physics-IQ A/B (GPT-5.6-Luna, 20 cases per arm)

All arms use the same model, inputs, 1280x720/24 FPS/120-frame contract, budget
and evaluator. Cost uses measured cached tokens and the reference tariff
(\$10/M uncached input, \$1/M cached input, \$50/M output). Every arm produced
20/20 valid RGB videos.

| Arm | Policy warn | Input | Output | Commands | \$/attempt | IoU v1 |
|---|---|---|---|---|---|---|
| Reference (frozen task text) | 10 | 149k | 13.7k | 11.1 | 0.99 | 0.0466 |
| Guidance (5 lines without `.tmp`) | 0 | 82k | 9.1k | 6.0 | 0.65 | 0.0496 |
| Exact guidance candidate (5 lines + `.tmp`) | 0 | 92.9k | 9.8k | 7.35 | 0.74 | Pending |
| Tool interface only | 0 | 143k | 15.4k | 9.3 | 1.05 | 0.0486 |
| Flash + tool interface | 0 | 84k | 11.4k | 6.4 | 0.76 | 0.0531 |
| 2 lines + tools | 0 | 126k | 13.6k | 9.7 | 0.92 | 0.0504 |
| 2 lines only | 10 | 138k | 13.6k | 11.8 | 0.95 | 0.0488 |
| 2 lines + `.tmp` | 1 | 132k | 13.2k | 10.3 | 0.93 | 0.0475 |

The earlier five-line arm cuts measured cost per attempt by 34% and input
tokens by 45%. The exact candidate saves approximately 25% and 38%,
respectively; its official quality evaluation is pending. The tool interface
adds commands and tokens without improving delivery or quality, so it is not
part of the final Flash. The 2-line ablations show that the caps and no-re-read
bundle, not the `/tmp` line, accounts for most of the saving.

IoU is the official Physics-IQ per-view weighted spatial IoU v1 on a 20-case
subset at the canonical 4 FPS masks; it is not the full 198-case Verified score.
Paired deltas against the reference are small and every 95% bootstrap interval
includes zero.

## Optional tool interface

`harness_variant: "flash-v1"` installs `INPUT_SUMMARY.json` plus
`pixelworld_run.py` and `pixelworld_check.py`; `harness_variant: "flash-lean"`
installs only the two helpers. These remain available for experiments with
models that need more scaffolding, but they are not the final Flash.

| File | Purpose |
|---|---|
| `INPUT_SUMMARY.json` | Canvas, prompt, required outputs, available Python packages, and first-frame statistics. Derived from inputs only; contains no generated pixels. |
| `pixelworld_run.py` | Runs `generate.py`, keeps stdout/stderr in `.tmp/generate_run.log`, prints the last 40 lines, then validates every required artifact. |
| `pixelworld_check.py` | Validates artifacts without regenerating: required files, frame count, tensor shape/dtype, PNG-to-tensor exactness, and frame 00 identity for I2V. |

Every run records `harness_variant` and the helper hashes in
`run_manifest.json`. The main frozen cohort stays on `baseline`.

## Run it

```bash
# Final Flash
python -m pixelworld.cli run-case \
  --config runs/luna_cost_ab/configs/flash_final.json \
  --model gpt56-luna --track vision-agent --case 0001 \
  --task-file runs/luna_cost_ab/task_flash_final.md

# Optional tool variant
python -m pixelworld.cli run-case \
  --config runs/luna_cost_ab/configs/flash_tools.json \
  --model gpt56-luna --track vision-agent --case 0001 \
  --task-file runs/luna_cost_ab/task_flash_tools.md
```

## Failure classification

A denied write to a nonexistent `/tmp` scratch path is recorded as a warning
(`failed_outside_scratch_operations`) when the traceback proves the write never
completed and the same path is not otherwise used. Missing or invalid delivered
artifacts still fail. This separates sandbox-path mistakes from model capability.
