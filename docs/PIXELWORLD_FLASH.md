# PixelWorld-Flash harness variant

PixelWorld-Flash is a **harness variant**, not a benchmark, model or renderer.
The task text, benchmark inputs, RGB output contract, evaluator and token budget
stay fixed. Only the agent's interface and process guidance change.

## What the tool variant adds

`harness_variant: "flash-v1"` in a benchmark config installs three files into
each prepared case workspace:

| File | Purpose |
|---|---|
| `INPUT_SUMMARY.json` | Precomputed canvas, prompt, required outputs, available Python packages, and first-frame statistics (size, mean/std RGB, 3x3 grid means, dominant colors, edge density). Derived from inputs only; contains no generated pixels. |
| `pixelworld_run.py` | Runs `generate.py`, keeps stdout/stderr in `.tmp/generate_run.log`, prints the last 40 lines, then validates every required artifact. |
| `pixelworld_check.py` | Validates artifacts without regenerating: required files, `frames/*.png` count, `pixels.npz` shape/dtype, PNG-to-tensor exactness, and frame 00 identity for I2V. Prints one compact JSON line. |

The variant also appends a short factual note to `GENERATION_RULES.md`: the
helpers above, the `artifact_io.save` signature, and that `/tmp` is outside the
workspace while `.tmp/` is writable. It does **not** cap commands, candidates or
visual inspections.

Every run records `harness_variant` and the helper/`INPUT_SUMMARY.json` hashes in
`run_manifest.json`. The main frozen cohort stays on `baseline`.

## Physics-IQ A/B (GPT-5.6-Luna, 20 cases per arm)

All arms use the same model, inputs, 1280x720/24 FPS/120-frame contract, budget
and evaluator. Cost uses measured cached tokens and the reference tariff
(\$10/M uncached input, \$1/M cached input, \$50/M output).

| Arm | Valid | Mean input | Mean output | Commands | \$/attempt | \$/valid |
|---|---|---|---|---|---|---|
| Reference (frozen task text) | 14/20 | 149k | 13.7k | 11.1 | 0.99 | 1.41 |
| Cost guidance only | 19/20 | 82k | 9.1k | 6.0 | 0.62 | 0.65 |
| Tool interface only | 20/20 | 143k | 15.4k | 9.3 | 1.05 | 1.05 |
| Guidance + tool interface | 20/20 | 84k | 11.4k | 6.4 | 0.76 | 0.76 |

The reference count is the corrected path-audit count: four additional runs had
valid videos but a denied `/tmp` scratch write. Six reference runs still fail
because their scratch script itself was written under `/tmp`, so the generation
never started.

The tool interface raises delivery reliability to 20/20 but does not reduce
tokens by itself. Prompt-level efficiency guidance is the dominant cost lever.
The combined arm tests whether reliability and cost can be obtained together.

## Run it

```bash
python -m pixelworld.cli run-case \
  --config runs/luna_cost_ab/configs/flash_tools.json \
  --model gpt56-luna --track vision-agent --case 0001 \
  --task-file runs/luna_cost_ab/task_flash_tools.md
```

`pixelworld_run.py` counts as a generate run in validation. Neither helper
writes RGB pixels or modifies submitted artifacts; they only read inputs and
validate outputs.

## Failure classification

A denied write to a nonexistent `/tmp` scratch path is recorded as a warning
(`failed_outside_scratch_operations`) when the traceback proves the write never
completed and the same path is not otherwise used. Missing or invalid delivered
artifacts still fail. This separates sandbox-path mistakes from model capability.
l. This separates sandbox-path mistakes from model capability.
