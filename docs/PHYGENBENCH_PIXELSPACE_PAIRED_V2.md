# PhyGenBench: direct pixels and paired initialization

The main experiment uses the 160 original PhyGenBench T2V prompts. The I2V
extension tests the same physical descriptions with an agent-constructed scene
whose appearance is re-rendered by Qwen-Image-2.1. It is a PixelWorld analysis,
not an official PhyGenBench I2V leaderboard.

## Four conditions

| Condition | Input to the video agent | Visual tools |
|---|---|---|
| T2V-Text | Original caption | Disabled |
| T2V-Vision | Original caption | Agent may inspect generated candidates |
| I2V-Text | Original caption + selected re-rendered initial image, readable through code | Disabled |
| I2V-Vision | Same caption and selected initial image, attached visually | Enabled |

All video outputs are 1280x720, 24 FPS, 120 RGB frames. Reuse matching T2V
deliveries. The downstream Text/Vision pair uses the same selected image hash.

## Scene construction pipeline

1. **Agent pixel first frame.** The coding agent writes and runs a program that
   saves one explicit RGB frame depicting the scene before the described action.
   Record the model, visual-access condition, code, candidate, task text and usage.
2. **Qwen appearance re-render.** Condition Qwen-Image-2.1 on that pixel image.
   Request photographic appearance while retaining the scene and pre-action
   state. Preserve candidates, model revision, seed, prompt, negative prompt,
   steps, CFG and selection evidence. This step uses an image model.
3. **Agent I2V.** Start a fresh ordinary PixelWorld case with the selected image
   and original caption. The coding agent computes every output RGB frame; frame
   zero must equal the selected conditioning image exactly.

Qwen participates only in stage 2. It does not generate, refine or continue the
video. Neither Qwen nor evaluator feedback is available to the downstream agent.
State whether the first-frame stage uses a common access condition and share
that stage across the downstream pair; otherwise initialization and visual access
would change together.

## Generation boundary

> Construct the video directly in image/pixel space. Do not build a separate
> scene/world model or physics simulation and render it into video.
>
> Make the entire scene as photorealistic as possible.

Local Python, NumPy/PIL, masks, coordinate functions, warps, compositing, texture,
lighting and motion calculations remain available. External generation models,
learned renderers, physics engines, network access and subagents are excluded
from video generation. `generate.py`, RGB candidates, tools and revisions are
retained externally.

## Current smoke and evidence

The published smoke is one GPT-6.1-Sol Vision case demonstrating the complete
agent-frame → Qwen re-render → I2V path. It is not a completed 160-case paired
evaluation or a four-domain smoke suite.

- Agent pixel frame: 1280x720, one saved frame.
- Qwen v1 retained a stylized appearance; v2 used a photographic prompt and a
  negative prompt excluding CGI, illustrations, labels and UI, with CFG 5 and
  50 steps.
- Native re-render size: 1280x704. The selected image is resized once to
  1280x720 before the I2V input is frozen; frame-zero validation uses that image.
- I2V: 120 frames at 24 FPS, artifact validation passed.

The run-local `smoke.json`, render logs and manifests provide exact settings and
hashes. Visual review must verify the pre-action state and scene content;
artifact validation alone does not establish physical quality.

[View the smoke](https://huggingfriends-pixelworld-dashboard.static.hf.space/phygen_firstframe_smoke.html).

## Evaluation and cost

Use the same PixelWorld GPT-5.5 judge for compared T2V and I2V videos. Label it
separately from official hierarchical PhyGenEval. Keep provided video-model v1
baselines distinct from refinement/V2V versions and document unavailable original
generation requests.

Report downstream video-agent cost and complete pipeline cost separately. The
latter includes agent first-frame preparation, Qwen rendering and I2V generation.
Shared initialization must not be charged twice to a Text/Vision pair without
stating that convention. Missing image-model billing or GPU-cost information is
unknown, not zero.
