# Provenance of retained benchmark results

A current generation config describes newly launched attempts. It does not
replace the configuration of an earlier accepted video. Preserve each retained
run's Codex version, requested effort, generation contract, budget, filesystem
policy, input hashes and output specification.

Budget and access policy are independent. A case-only run may have an earlier
output-token budget. Do not infer its access boundary from its budget, or
relabel every result as using the latest config.

## Audit the videos actually scored

Follow evaluator-selected sources rather than selecting a different successful
attempt from the generation directory:

- Physics-IQ: `generated_videos_5s/input_manifest.json`, including source-run,
  manifest and pixel hashes.
- RoboJudge: the evaluation `plan.json` records and their selected run paths.
- PCVE: the exported `pixelworld_submission.json` rows and source-manifest hashes.
- PhyGenBench: the judge's per-case records and their source runs.

Validate the exact official case count and unique case IDs for each complete
cohort. Read configuration fields from those runs' manifests. Check recorded
source hashes where available, and leave missing historical hashes unknown;
computing a current hash does not establish that it was recorded during scoring.

The audit is descriptive metadata evidence. It does not itself establish that
an agent followed every behavioral restriction, accessed no outside file, or
would produce identical results under a different budget or sandbox. Controlled
analyses must state their eligible configuration strata. Compatible historical
outputs remain available with their original provenance.

Audit datasets and reproduction scripts belong in the public
[Hugging Face results repository](https://huggingface.co/datasets/HuggingFriends/PixelWorld/tree/main/analysis/provenance).
Generation videos, traces, source references and measured scores stay outside
the code Git repository.
