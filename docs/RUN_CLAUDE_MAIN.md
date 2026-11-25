# 合作者任务：Claude 主实验

你负责用 Claude 模型完成 PixelWorld 的四个主 benchmark，**每个 benchmark 全量、只跑 Vision 主实验**。每个模型共 537 个官方样本。记录实际使用的确切模型版本/API model ID。

代码：<https://github.com/SiYuanJasperMa/PixelWorld>

主协议：[MAIN_SUITE_20261124.md](MAIN_SUITE_20261124.md)  
运行步骤：[ADD_MODEL_RUNBOOK.md](ADD_MODEL_RUNBOOK.md)  
结果页：<https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html>

## 需要跑什么

| Benchmark | 任务 | 全量范围 | 输出配置 | 真实 attempt 上限 |
|---|---|---:|---|---:|
| Physics-IQ Verified | I2V | 198 个样本 | 1280×720，24 FPS，120 帧，5 秒 | 每样本最多 5 次 |
| RBench Common Manipulation | I2V | 官方 Common Manipulation 子集 50 个样本 | 保留原比例，短边 720，24 FPS，120 帧，5 秒 | 每样本仅 1 次真实生成 |
| PhyGenBench | T2V | 160 条官方 prompt | 1280×720，24 FPS，120 帧，5 秒；场景尽量具有摄影真实感 | 每样本最多 5 次 |
| PCVE-RigidBench | V2V 编辑 | 官方完整 129 个样本 | 1280×720，24 FPS，96 帧，4 秒；保留官方编辑时间线 | 每样本最多 5 次 |

“Claude”在此指被评测的模型。**主实验统一使用 Codex CLI 0.169.0**，不是 Claude Code harness 对照实验。使用当前冻结的 prompt、输入 manifest 和 benchmark 配置；只修改 Claude 的模型/provider 信息与指定保存目录。

## 固定规则

- Track 固定为 `vision-agent`，论文中名称为 Vision；允许读取规定的输入像素及检查自己的输出。
- 使用 `case-only-v1` 隔离、baseline harness，以及请求的 high reasoning 设置。保存实际模型参数；接口不支持指定设置时，记录并反馈，不静默替换。
- 每次真实 attempt 的预算为 **1,000,000 个已报告 output tokens**。保存 input、cached input、output 等实际 usage，缺失值记录为 unavailable。
- Coding agent 自己写代码并输出每一帧 RGB。不得调用图像/视频生成模型、学习型渲染器、物理引擎、额外子 agent 或评测器反馈来生成视频。
- 有效交付不重跑。已完成且通过配置/输入/产物兼容核验的 Claude 结果可以复用，保留原始来源。
- 基础设施错误不占真实 attempt，但必须有错误证据，保留原记录。真实生成/交付失败受上述上限约束。
- RBench 每样本只允许一次真实生成，不做 Best-of-N、质量重抽或第二个真实候选。PCVE 使用完整 129 条，不另做 object removal 子集表。
- 任务、缓存、原始 I/O、完整轨迹、代码、视频、配置和 usage 保存在指定云盘；私有原件独立校验后才清理可再生临时产物。

## 执行顺序

1. 拉取约定的 GitHub `main` 提交，记录 commit、Codex 版本/可执行文件哈希和确切 Claude model ID。领取四个冻结配置及允许使用的输入 manifest。
2. 每个 benchmark 先跑 1 个标准配置的 smoke，检查模型路由、图像/工具、隔离、RGB 交付、token 记录及对应评测适配。通过的 smoke 留在全量 run root，计入全量并复用。
3. 验收通过后分别启动四个全量矩阵，持续保存所有 attempt。不要改 prompt、画布、FPS 或候选选择规则来改善分数。
4. 采用下面的评测口径。可以增量准备/评分，但只有范围完整、来源匹配时才能报告完整 benchmark 分数。
5. 按 [HF_PUBLISHING.md](HF_PUBLISHING.md) 上传视频和脱敏完整 trace，交回模型/benchmark/run 索引与上传收据，让主页接入结果。

## 评测与交回内容

| Benchmark | 主分口径 |
|---|---|
| Physics-IQ | 固定版本的官方 Verified evaluator |
| RBench | 固定官方 Common Manipulation GPT 分支，VLM 使用 GPT-6.1-Sol；不是 GPT＋Qwen 合榜分 |
| PhyGenBench | 当前已确认的 GPT-6.1-Sol 三阶段 MCQ 适配；与旧 judge/原榜分开标注 |
| PCVE | 固定官方完整 129 条评测，主表使用 PES |

交回：四组逐样本生成状态、全部 attempt/失败原因、精确配置与输入哈希、原始 I/O 和完整轨迹的私有归档地址及校验收据、实际 token 用量、视频/代码、评测版本与得分覆盖，以及 HF 视频/trace 链接。未齐范围明确标为 partial；不删除失败样本来凑完整分数。

主观画质与 FVD 是团队单独汇总的补充指标。生成方交回稳定的 accepted-run/视频哈希索引即可，不自行更换 judge 或参考集。

## 本次任务范围之外

本次不跑 Text、Vision-Input、分辨率/FPS/thinking mode 对照、OpenCode/Claude Code harness 对照、RoboJudge、Pi/DSH、Flash 或首帧生成方案。分析实验由团队另行负责。

