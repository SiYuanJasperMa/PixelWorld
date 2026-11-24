---
license: other
task_categories:
- image-to-video
- text-to-video
- video-to-video
tags:
- pixelworld
- coding-agent
- physics
---

# PixelWorld: generated videos and agent traces

[Dashboard](https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html) ·
[Code and current protocol](https://github.com/SiyuanMaCS/PixelWorld) ·
[Upload guide](https://github.com/SiyuanMaCS/PixelWorld/blob/main/docs/HF_PUBLISHING.md)

Coding agents write Python and emit RGB video frames directly. The current
main suite contains Physics-IQ Verified (198 I2V cases), RBench Common
Manipulation (50 I2V cases), PhyGenBench (160 T2V prompts), and PCVE-RigidBench
(129 V2V edits). Main quality is 720p at 24 FPS; PCVE retains its 4-second
editing timeline, the other tasks use 5 seconds. The code repository documents
protocol versions and judge adaptations.

The roster includes GPT-6.2-Sol, GPT-6.1-Sol, GPT-6-Astra, GPT-5.6-Sol,
GPT-5.6-Terra, GPT-5.6-Luna, DeepSeek-V4.1-Flash, and Seed-2.1-Pro-0915.
A registered model may still be generating or uploading. Use the coverage
report, not this card or the repository file count, to check completeness.

| Entry | Contents |
|---|---|
| [metadata/main_suite_status.json](metadata/main_suite_status.json) | Current model × benchmark progress and objective scores |
| [metadata/experiment_index.json](metadata/experiment_index.json) | Current main suite and harness index |
| [metadata/publication_coverage.json](metadata/publication_coverage.json) | Per-group current detail hashes and upload receipts |
| [live/catalogue.json](live/catalogue.json) | Current per-case discovery; includes historical groups |
| `live/<suite>/<model>/<track>/index.json` | Cases and permanent attempt record IDs |
| `live/<suite>/<model>/<track>/<record>.json` | Prompt, generation code, tokens, validation and candidate links |
| `live/<suite>/<model>/<track>/<record>.trace.tar.gz` | Sanitized full native trace, recorded tool images and code revisions |
| `data/assets/<hash>/` | Shared immutable video previews and posters |
| `data/`, `traces/` | Earlier compatible snapshots and aggregate trace bundles |
| `baselines/` | Separately identified video-generation comparators |
| `analyses/`, `analysis/` | Existing analysis artifacts, including retired conditions |

Existing paths remain for reproducibility. RoboJudge, Pi and retired Flash
artifacts are historical; they do not define the current main suite. Consult
the current index before pooling conditions. Objective physics scores,
subjective appearance scores and FVD remain separate metrics.

The budget is 1M reported output tokens per real attempt. Existing accepted
100k-budget deliveries are retained with their original budget. The ordinary
cap is five real model attempts; RBench permits one real candidate per sample.
Infrastructure records do not spend those caps and may produce a later record
ordinal. Failed attempts retain evidence and have no fabricated video.

The dashboard fetches the dataset status every 60 seconds and updates its main
table when the paired snapshot is available. The publisher checks every five
minutes. Video/trace uploads run independently; the coverage report describes
matching upload receipts, rather than a full remote byte audit.

Public exports exclude credentials, private machine paths, licensed standalone
benchmark inputs, references/masks and evaluator ground truth. Original API
request/response bodies and private readback archives remain in the owner's
private storage. A preview is compressed for browsing; official evaluation
uses the submitted RGB output. A source archive can be incomplete if native
recording was interrupted; its trace-status fields retain that limitation.
