# PixelWorld：15分钟外部运行预算

已撤回：用户随后明确要求按token限制，以避免限流和API等待影响。本文只保留为时间预算原型记录；实现已撤回，当前harness没有启用900秒默认截止。新的token档位等待用户选择。

2026-11-08：用户选择15分钟时限。新配置的默认值为：

```json
{
  "wall_time_budget_seconds": 900
}
```

这是harness外部控制，数值不进入agent任务。新最小任务仅增加“避免没有明确改进目标的反复检查或重复执行”的一句提醒，仍允许自主生成、检查和修订。

## 计时和停止

- 从agent CLI启动计时，包含API等待、模型调用和工具执行；输入准备、排队、评测、发布及截止后的机械收尾不属于这段模型运行预算。
- 同一模型/track/case的恢复attempt共享900秒，使用已记录的agent耗时，不能重开一次就重新拿15分钟。
- 到期终止CLI及其拥有的本地进程组。信号和实测停止时间写入watchdog记录。
- 选择截止前已冻结、校验通过的最后候选，按时间选择，不使用评测分数。中断中的产物保留在workspace外的`interrupted_artifacts/`，不混入提交。
- 格式、首帧、PNG/tensor一致性及工具/track审计保持有效。预算提交仅允许缺少自然完成事件，不伪造CLI成功退出；实际退出码与`completion_mode=budget_frozen`均保留。
- 没有有效候选时记录budget exhaustion，停止该case的自动重试；其他case仍可继续。不能用静止首帧或外部绘制伪造agent提交。

## 配置和旧结果

非legacy配置默认900秒；legacy v1未明确指定该字段时保持原来设置。`null`可用于明确标记的无时限对照。旧产物和旧实验不重写、不重启。

运行manifest记录预算、先前attempt消耗、启动时剩余量、实际agent耗时、终止原因以及选择的候选。WBench等多轮任务每次视频生成单独计时，并保留episode总耗时。

## Token记录

有完整终端usage时保存API上报总量。因中断而缺少完整usage时，保存full rollout中已完成请求的去重累计量为`completed_request_usage`，并标记`inflight_usage_unknown=true`。该量是下界，不填成精确的最终output总量，也不按时间比例估算。

15分钟是本次选定的运行时间条件，不是等token/等算力条件。实际tokens、API服务和并发设置需要与结果一起报告。此前32k token建议保留为分析资料，当前没有同时启用32k截断。

## 历史数据图

`runs/token_budget_review_20261108/tokens_15min.png`与PDF显示完整历史run的output分布，以及15分钟内首次保存/完整结束的记录比例。它不是重新运行后的15分钟成绩；超过15分钟且没有请求级历史的旧run，其前15分钟tokens仍未知。

历史Seed Pro在15分钟内保存候选的比例较低：Text 6/63，Vision-Agent 1/52。需要把预算不足与内容错误分开报告。Physics-IQ的完整官方评测仍要求完整198条合法输出；有缺视频的预算条件不能直接宣称得到完整官方成绩。
