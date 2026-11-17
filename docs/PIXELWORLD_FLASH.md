# PixelWorld-Flash development

**Status: no final Flash version is accepted.** The baseline remains the default
for main experiments. Experimental arms are being compared on Physics-IQ.

Flash aims to reduce cumulative input and output tokens while retaining quality
or allowing only a small decrease. It can change the tool interface and task
delivery process. Benchmark inputs, direct RGB generation, output artifacts,
source/data pins and the baseline protocol remain protected.

## Current interventions

| Intervention | Preserved behavior | Current decision |
|---|---|---|
| Core task tools | exec, write_stdin, apply_patch and view_image; full task and conversation | Native24 smoke passed; matched20-scene comparison running |
| Retrievable old tool returns | Original calls and executable patch text, source inputs, task discovery and recent inspections | Native24 smoke passed; separate matched20-scene comparison running |
| Exact initial task pack | Unchanged prompt/rules/spec text and autonomous observation/revision | Matched comparison running; failures and spend retained |
| Compact save API and receipt | Original RGB writer; receipt reports writing, not final harness acceptance | Standalone tests did not reduce measured cost |
| Aggressive call/revision guidance | Original RGB artifact contract | Does not meet the proposed quality-loss criterion; not selected |

The core-tool transport removes general user-input and goal-management schemas.
It does not impose observation or revision caps. The history transport archives
older eligible results within the same case; source images and task-discovery
results remain in context. Each archive is retrievable, and the full external
trace is retained. No tool synthesizes pixels or adds a learned renderer.

These are independent development arms. Their eventual combination or selection
requires matched evidence, new validation cases and cross-model confirmation.
No previous five-line guidance is designated a final Flash task.

## Quality and cost accounting

Primary main-setting quality uses all120 native24FPS frames, case-specific masks
and the pinned official per-view metric functions. Earlier4FPS-decimated scores
remain explicitly labeled development measurements. Neither is the full198-case
Verified score. Full official main scores use the complete corresponding scope.

Report RGB validity, protocol acceptance, infrastructure interruption, measured
usage coverage, and paired quality separately. Cost per accepted delivery includes
measured unsuccessful-attempt expenditure. Measured-cache costs and the user
requested70% cache scenario are separate estimates; free test models incur no
claimed bill. The proposed relative5% loss criterion is a development choice,
not an official Physics-IQ property.

The old degraded-image-tool cohort and scores affected by the shared-mask bug
cannot support final quality claims. Unverified historical quality numbers have
been removed from this document.

- [Validation protocol](FLASH_VALIDATION_PLAN.md)
- [Trace-grounded analysis](PIXELWORLD_FLASH_ANALYSIS.md)
- [Live cost, quality, videos and full traces](https://huggingfriends-pixelworld-dashboard.static.hf.space/flash.html)
- [Measured native24 report](https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/analyses/flash/confirmation_native24.json)
