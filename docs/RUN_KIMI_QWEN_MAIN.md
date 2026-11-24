# 合作者任务：Kimi 与 Qwen 主实验

你负责用 **Kimi 和 Qwen 两个模型分别**完成 PixelWorld 的四个主 benchmark。两者都全量、只跑 Vision 主实验；每个模型 537 个官方样本，合计 1,074 个模型×样本任务。分别记录实际使用的确切模型版本/API model ID，分别保存和评分。

代码：<https://github.com/SiyuanMaCS/PixelWorld>  
主协议：[MAIN_SUITE_20261124.md](MAIN_SUITE_20261124.md)  
运行步骤：[ADD_MODEL_RUNBOOK.md](ADD_MODEL_RUNBOOK.md)  
结果页：<https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html>

## 两个模型各自需要跑什么

| Benchmark | 任务 | 每个模型的全量范围 | 输出配置 | 真实 attempt 上限 |
|---|---|---:|---|---:|
| Physics-IQ Verified | I2V | 198 个样本 | 1280×720，24 FPS，120 帧，5 秒 | 每样本最多 5 次 |
| RBench Common Manipulation | I2V | 官方 Common Manipulation 子集 50 个样本 | 保留原比例，短边 720，24 FPS，120 帧，5 秒 | 每样本仅 1 次真实生成 |
| PhyGenBench | T2V | 160 条官方 prompt | 1280×720，24 FPS，120 帧，5 秒；场景尽量具有摄影真实感 | 每样本最多 5 次 |
| PCVE-RigidBench | V2V 编辑 | 官方完整 129 个样本 | 1280×720，24 FPS，96 帧，4 秒；保留官方编辑时间线 | 每样本最多 5 次 |

共八组完整结果：Kimi 的四个 benchmark，Qwen 的四个 benchmark。主实验统一使用 **Codex CLI 0.169.0**；使用当前冻结的 prompt、输入 manifest 和配置，只修改相应模型/provider 信息与指定保存目录。

## 固定规则

- Track 固定为 `vision-agent`（论文名称 Vision）、`case-only-v1` 隔离和 baseline harness；请求 high reasoning，记录实际接口设置与不支持的参数，不能静默替换。
- 每次真实 attempt 的预算为 **1,000,000 个已报告 output tokens**。完整记录 input、cached input、output 等 usage；缺失值为 unavailable，不当作 0。
- Kimi 与 Qwen 的模型 key、run root、缓存、会话、usage 和评分分开，不能互相补帧、替换失败结果或混算均值。
- Coding agent 自己写代码并输出每帧 RGB。不得调用图像/视频生成模型、学习型渲染器、物理引擎、额外子 agent 或评测器反馈来生成视频。
- 有效交付不重跑。已有同模型结果经过配置/输入/产物兼容核验后可以复用，保留来源。
- 基础设施错误不占真实 attempt，但必须保留证据与原记录；真实生成/交付失败受上限约束。
- RBench 每样本只允许一次真实生成，不做 Best-of-N、质量重抽或第二个真实候选。PCVE 评完整 129 条，不另做 object removal 子集表。
- 使用指定云盘保存任务、缓存、原始 I/O、完整轨迹、代码、视频、配置和 usage；独立归档校验后才清理可再生临时产物。

## 执行顺序

1. 记录约定的 GitHub `main` commit、Codex 版本/可执行文件哈希，以及 Kimi、Qwen 的确切 model ID。领取四个冻结配置和允许使用的输入 manifest，为两个模型分别建立配置/run root。
2. **每个模型、每个 benchmark 各做 1 个 smoke**，共八个。检查实际模型路由、图像/工具、隔离、RGB 交付、usage 和对应评测适配；通过的 smoke 计入全量并复用。
3. 验收后分别启动八组全量矩阵。可按资源并行，模型/样本的隔离与记录必须独立。不要为提高分数改 prompt、分辨率、FPS 或候选规则。
4. 按下面的主分口径准备并评分。增量分明确标 partial，完整分必须有完整范围和匹配来源。
5. 按 [HF_PUBLISHING.md](HF_PUBLISHING.md) 上传视频、脱敏完整 trace 和状态索引，交回收据以接入主页。

## 评测与交回内容

| Benchmark | 主分口径 |
|---|---|
| Physics-IQ | 固定版本的官方 Verified evaluator |
| RBench | 固定官方 Common Manipulation GPT 分支，VLM 使用 GPT-6.1-Sol；不是 GPT＋Qwen 合榜分 |
| PhyGenBench | 当前已确认的 GPT-6.1-Sol 三阶段 MCQ 适配，与旧 judge/原榜分开标注 |
| PCVE | 固定官方完整 129 条评测，主表使用 PES |

这里的 Qwen 是**生成侧被评测的 coding-agent 模型**；RBench 的评分仍遵循团队固定 GPT-6.1-Sol 分支，不添加 Qwen judge 来改成合榜口径。

每个模型分别交回：四组逐样本状态、全部 attempt 与失败原因、精确配置/输入哈希、原始 I/O 和完整轨迹的私有归档地址与独立校验收据、实际 token 用量、视频/代码、评测版本/得分覆盖，以及 HF 视频/trace 链接。失败和未完成样本必须保留；不通过删样本改变分母。

主观画质与 FVD 由团队单独汇总。交回稳定的 accepted-run/视频哈希索引即可，不自行换 judge 或参考集。

## 本次任务范围之外

本次不跑 Text、Vision-Input、分辨率/FPS/thinking mode 对照、OpenCode/Claude Code harness 对照、RoboJudge、Pi/DSH、Flash、Qwen-Image 首帧或其他生成增强方案。分析实验由团队另行负责。

