# Efficiency prompt study

This study tests whether explicit instructions to save promptly, revise concrete
defects, and finish once the task is complete reduce token use while preserving
Physics-IQ quality. The baseline generation interface remains in use.

Only the baseline sentence `Aim for high-quality output while considering
efficiency.` is replaced by [EFFICIENCY_GUIDANCE.md](../../../pixelworld/assets/EFFICIENCY_GUIDANCE.md).
The task introduction, generation rules, scratch instructions, model tools,
image access, reasoning effort, output-token allowance, and RGB contract are
preserved. There is no fixed command count or candidate count and no automatic
stop after saving a candidate. Models may inspect and revise their outputs.

The development study uses GPT-5.6-Luna, Vision, 720p at 24 FPS, and twenty
previously selected Physics-IQ scenarios. Existing baseline videos and their
native-frame scores are reused; they are not generated again. This is a
development comparison, not an independent confirmation or a full benchmark.
Five real attempts are allowed per case; positively identified infrastructure
interruptions remain separate. Unknown prior usage is excluded from complete
cost comparisons rather than assigned zero cost.

`tools/run_flash_prompt_study.py --study STUDY_DIR` runs the unchanged
`pixelworld run-case --task-file` interface from a frozen code snapshot. The
study directory contains:

- `tasks/baseline.md`, `tasks/efficiency.md`, and their exact diff;
- `experiment_manifest.json`, including baseline record identities;
- `configs/prompt.json` and `code_snapshot_hashes.json`;
- original attempt records, native usage, code, candidates, and tool traces;
- `smoke_gate.json` and the controller status.

The first listed case is the smoke. Expansion is permitted only after delivery,
complete recorded usage, and agreement with the baseline's initial input
hashes, image/tool boundary, Codex version, reasoning effort, and token budget.
Failed smoke attempts are preserved and use the normal retry accounting.

The evaluator uses the pinned Physics-IQ implementation and all native 24 FPS
submission frames. Results report matched case identities, quality changes,
input/output tokens, tool calls, delivery rate, and costs including failed
attempts. The 70% cache estimate and costs using measured cache usage are both
labeled. Runtime comparisons are observational: the reused baseline predates
the current asynchronous cloud-logging transport.

The Flash dashboard has a separate `prompt_native24` selector. Progress and
quality reports are generated with the existing Flash analyzer. New studies
can be added through `watch_flash_analysis --study NAME ROOT`; a dedicated
publisher can use `--only-extra-studies` without replacing other live reports.
Videos and complete sanitized traces are published through the existing HF
pipeline. Raw scores and generated artifacts stay outside the code repository.

No quality-preservation or cost-reduction claim is accepted from a single smoke.
Independent held-out confirmation is required before choosing a final Flash
configuration.
