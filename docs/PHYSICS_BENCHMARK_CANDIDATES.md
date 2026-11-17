# PixelWorld 物理评测候选

资源核对日期：2026-11-07。选择更新：2026-11-09。物理与内在真实性候选保留Physics-IQ、VBench-2.0 I2V和PVBench；Principia已按用户要求排除。PVBench尚未启动生成或评测；MORPHEUS保留历史备选，统一2.7秒生成建议已撤回。

| Benchmark | 在研究中的作用 | 规模口径 | 当前状态 |
|---|---|---|---|
| Physics-IQ Verified | 真实实验的整体时空/像素匹配；主全量数据与trace分析 | 198输入，66场景×3视角 | 已有完整结果，保留冻结协议 |
| VBench-2.0 I2V | 补充生成内在真实性的评测 | 按完整官方I2V设置核对 | 保留候选；官方I2V输入、提交与评测设置待核对 |
| PVBench | 更广的物体几何、状态、运动与机械约束 | 论文/README报告600组匹配T2V/I2V任务，46维、其中25维属于物理层 | 评测源码公开；完整benchmark资源另行维护，未确认600条I2V可获取 |
| MORPHEUS | 轨迹运动方程与物理不变量；保留候选 | 论文约130真实录像；当前公开现实数据124；Wan I2V公开包96 | 公开包与论文的帧数/样本口径有差异，正式运行参数尚未固定 |

## Principia（历史调研，已排除）

2026-11-09用户明确不纳入后续实验。以下仅保留已完成的资源调研，不安排生成或评测。

官方来源：[项目页](https://principiabench.github.io/)、[代码](https://github.com/PrincipiaBench/Principia)、[数据](https://huggingface.co/datasets/varunvarmat/Principia)。项目页标注NeurIPS 2026接收。

本次逐目录核对公开HF输入：

| 生成文件夹 | 独立输入数 |
|---|---:|
| coeff_friction | 102 |
| conservation_of_momentum | 56 |
| moment_of_inertia | 61 |
| pendulum | 65 |
| projectile | 55 |
| restitution | 69 |
| spring | 49 |
| 合计 | 457 |

`gravity`从`restitution`的同一批视频计算，不增加独立生成任务。论文报告401实录场景、扩展至529场景；不将其直接当作当前公开包的独立生成数量，也不把统计差异直接判断为缺失数据。正式运行冻结公开manifest、数据revision和代码commit，再核对论文对比口径。

输入为官方`frame_0000.png`和`prompts.json`中的文本。`conservation_of_momentum`按官方样本范围选择两种对应prompt。原图大多1280×720，restitution和spring为720×1280；harness必须保持横竖比例。评测`metadata.json`中的标注点和参数留在外部，不作为生成反馈。

评测流程：SAM3跟踪→每case物理测量→方向一致性过滤→各scenario评分与汇总。连续评分比较两个量的对称相对差；projectile与conservation_of_momentum主要检查排序，不称为完整的绝对物理量或动量残差。缺少视频、缺少测量、过滤失败与被忽略样本分别记录；官方代码会跳过缺失case，因此外部完整性校验必须保证提交范围完整。

SAM3权重需要访问权限，官方README要求CUDA GPU。这是评测端依赖；当前没有核实本机模型访问/安装状态。`--max-frames=240`是评测读取上限，不是生成帧数要求。时长、原生FPS、输出分辨率需核对官方生成/基线配置后确定；不套用MORPHEUS样例或Physics-IQ的规格。

## PVBench

官方来源：[项目页](https://kszg-11.github.io/PVBench-Website/)、[代码](https://github.com/KSZG-11/PVBench)、[论文](https://arxiv.org/abs/2610.26711)。

物理层25维在当前README中分为17项专用模型量化、4项测量与MLLM混合、4项纯MLLM。因此要分别分析量化部分和判读部分，不能把整套总分称为直接物理量测量。

代码提供`evaluate.py`、batch入口、模型配置与示例manifest。README同时明确：公开发布仍受项目发布审查条件限制；私有benchmark资源与生成产物独立维护。还未确认600组完整首帧/prompt、必要标定或独立真值以及可比较baseline材料均可获取。当前源码可见不等于完整官方评测可以复现。

后续先核对完整输入bundle、官方输出规格、逐维依赖/真值、评分版本和公开baseline口径；条件齐备后再做官方smoke与全量。不自行构造600条任务，不用示例manifest替代完整官方数据，也不以harness自定义数据得到的分数冒充PVBench榜单成绩。

2026-11-07源码进一步核对：PVBench是“条件/场景判据→专用模型测量或MLLM判断→逐维1–5分→分组汇总”的混合管线。普通I2V的独立真值不由条件首帧自动产生。`core/pipeline_physics.py`实际只从外部kwargs/manifest读取GT；没有GT时使用proxy路径。带独立仿真GT的精确误差路径与普通I2V区分，不把专用模型预测用作自己的真值。

- R15/R16：SAM3与SpatialTracker提取物体轨迹，再估计位移、速度、加速度。无GT时主要检查场景方程拟合、匀速/匀加速的一致性；有GT时才比较轨迹/速度误差。
- R23：优先用逐帧SAM3检测碰撞与前后速度；质量比可以来自prompt，部分回退路径从速度变化反解。因此其动量残差依赖质量/轨迹假设，不能当作直接测得的绝对动量。
- R24：构造单位质量的动能与势能曲线。可估计尺度时使用米制近似；无法锚定尺度时使用估计重力的无量纲能量，检查能量异常增长、反弹高度和恢复系数变化，返回proxy状态。
- 材料、热学、电磁、相变模块使用MLLM。各项得分不具有相同的物理量测量含义。
- `evaluators/base.py`区分ok/proxy/degraded/error/skipped/unobservable。当前均值计入前三者，错误、跳过、不可观测项不计入，因此必须同时报告逐维覆盖、状态和失败数；总体均值不等于全部物理性质已被验证。
- 配置中的`max_frames=32`为评测抽帧设置；运动学和动力学可读取全帧，上限默认300。它们都不是生成任务的目标帧数或官方统一时长。

源码依据：[运动学](https://github.com/KSZG-11/PVBench/blob/main/evaluators/physics/kinematics.py)、[动力学](https://github.com/KSZG-11/PVBench/blob/main/evaluators/physics/dynamics.py)、[评分状态与聚合](https://github.com/KSZG-11/PVBench/blob/main/evaluators/base.py)。这些描述针对当前公开实现，尚未与完整600条官方资源及论文baseline逐项复现核对。

## 执行边界

- 新benchmark生成前必须找到前沿视频/世界模型的公开分数，并确认它与我们拟用的完整官方I2V设置、指标及聚合口径相匹配。先整理baseline证据，再运行PixelWorld生成/评分smoke。找不到可比分数就不进入正式评测，不安排baseline生成或重评。
- Physics-IQ主实验和原生自主修订保留。新增benchmark分别适配输入和提交规格，保持cleanroom、工具边界、外部trace记录等大结构。
- 准备VBench-2.0 I2V和PVBench的官方协议及资源。Principia不进入后续实验。PVBench同时明确完整数据可获取状态；不因为尚未获取其资源而停止其他可执行分析。
- 首先用GPT-6.1-Sol / Vision-Agent验证正式输入、产物和评测链路；smoke仅检验运行，不当作完整benchmark成绩。扩大模型/track矩阵前核算新增视频量。
- 正式benchmark使用完整所选官方输入范围。个案/原子能力诊断另列，不冒充子集排行榜。各benchmark分数分别报告，不混成新的总分。
- 保留原生FPS、帧数、时长与评测预处理后的FPS/尺寸，两者不混用。没有统一硬校验不等于可以任意选择生成时长。
