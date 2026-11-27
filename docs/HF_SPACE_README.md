---
title: PixelWorld Live
emoji: 🌍
colorFrom: blue
colorTo: green
sdk: static
pinned: false
---

# PixelWorld Live

[Open the dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html) ·
[Videos and traces](https://huggingface.co/datasets/HuggingFriends/PixelWorld) ·
[Code and protocol](https://github.com/SiYuanJasperMa/PixelWorld)

This static Space displays the current four-benchmark suite: Physics-IQ
Verified (I2V198), RBench Common Manipulation (I2V50), PhyGenBench (T2V160),
and PCVE-RigidBench (V2V129). RoboJudge and superseded conditions remain in the
historical pages. Current main generation uses 720p/24 FPS; PCVE is four
seconds and the other tasks are five seconds.

Model-by-benchmark cells open a common sample browser with 24-card pages,
search, attempt history, video candidates, recorded tokens and trace links.
Old benchmark URLs with `?model=...` remain usable. The native GPT-6.2-Astra
cohort is labeled Codex 0.172.0/high/default; the TRAEX main baseline remains
Codex 0.169.0. Full benchmark scores require complete matching evaluator
coverage. **Gemini 4 Hydrogen High** is a normal main model, using native
Antigravity CLI 1.5.18 ACP and the same four main media contracts. Its main
cells open the same sample browser, with video, trace, token and publication
coverage records. Physical QA, subjective video quality and FVD are separate.

The dataset is the data source. The main page polls
`metadata/main_suite_status.json` every 60 seconds and replaces its content with
the paired `dashboard/index.html` snapshot when available. Detail pages read
`live/catalogue.json` and recorded case indices; previews/posters are under
`data/assets/`. The main snapshot publisher checks every minute, while
case, media, trace and progress uploaders run independently. Refreshing the
page does not generate or score a video.

The retained analyses include Text/Vision, Physics-IQ resolution/FPS/thinking,
and the same-model Codex/OpenCode/Claude Code comparison, including GPT-6.2-Sol.
Config admission, generation, scoring and publication are distinct states.
Original API bodies and private archives stay outside this public Space.

[Uploading and publishing](https://github.com/SiYuanJasperMa/PixelWorld/blob/main/docs/HF_PUBLISHING.md) ·
[Collaborator run instructions](https://github.com/SiYuanJasperMa/PixelWorld/blob/main/docs/COLLABORATOR_HANDOFF.md)
