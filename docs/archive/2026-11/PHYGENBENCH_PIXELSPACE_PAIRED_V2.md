# PhyGenBench: direct pixels and paired initialization

The main experiment uses the 160 original PhyGenBench T2V prompts. The I2V
extension tests the same physical descriptions with an agent-constructed scene
whose appearance is re-rendered by Qwen-Image-2.1. It is a PixelWorld analysis,
not an official PhyGenBench I2V leaderboard.

This is a diagnostic comparison of initialization conditions. Retain and report
the observed outcomes whether re-rendering helps or hurts. Downstream video
scores do not determine image selection, admission of a valid sample, or further
prompt tuning. Image selection checks only the declared scene and pre-action
input condition.

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

The current v3 smoke has valid GPT-6.1-Sol Text and Vision deliveries for one
prompt. It is not a completed 160-case paired evaluation or a four-domain suite.

- The agent pixel frame is 1280x720, with one saved frame.
- Three Qwen candidates were produced on a native 1536x864 canvas. The selected
  pre-action image is uniformly resized to 1280x720, without stretching objects.
- Both downstream tracks use the same frozen image hash and produce 120 frames
  at 24 FPS. Frame zero matches the conditioning image exactly.
- Token usage, time, videos and physical-judge results are external artifacts.
  A valid delivery is retained even when its physical-quality score is low.

The run-local `smoke.json`, render logs and manifests provide exact settings and
hashes. Visual review must verify the pre-action state and scene content;
artifact validation alone does not establish physical quality.

Earlier pilots remain available for provenance. Qwen v1 retained stylized
appearance. The earlier v2 used a native 1280x704 canvas resized to 1280x720.
A subsequent visual audit rejected that v2 image as a pre-action initialization:
it added a stream flowing into the cup from above. The source agent frame and
downstream I2V program were inspected; their operations remain in 2D pixel space.
The existing video remains a pipeline pilot, not an accepted paired analysis
sample. Three revised candidates were generated with an explicit unchanged pre-action
state instruction. Visual inspection selected one image with no added incoming
stream. The native canvas is 1536x864 (both dimensions divisible by 32), allowing
uniform resizing to 1280x720 without stretching objects. Both downstream tracks
use the same selected image hash and pass RGB/timeline/first-frame validation.
The Text agent has neither an image attachment nor visual tools; the Vision
agent actually exercised its visual tools. Judge results and token/time records
remain external. Physical quality is assessed separately from artifact validity;
this pilot does not establish a general physical-score improvement.

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
