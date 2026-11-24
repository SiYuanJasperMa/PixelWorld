# Local MiniMax H3 on PhyGenBench

Use the official160 `prompts.json` captions, in the original question order,
with no appended instructions, input images, reference videos or judge feedback.
The full `MiniMaxAI/MiniMax-H3` checkpoint revision is
`42ed227ee7df40d41602854ae760620d6eb651fe`. The clean FastVideo runtime is
`d995516da00c24105aa841df1e690d3cd8a6c173` and the native T2V pipeline is
`MiniMaxH3ModularPipeline`, distinct from the PCVE Ref2VA workload.

Native generation:1344x768,24FPS,124 frames,50 sigma-grid points,CFG1,seed42,
BF16 transformer, original video VAE. Export first120 RGB frames at24FPS without
time rescaling, yielding the same five-second judge window as coding agents and
Seedance. Preserve full raw output. GPU parallelism/offload is an execution
setting and must not change prompts, sampling or model weights.

The first video is the delivery smoke. It must pass frame/FPS checks before the
persistent generator continues to the remaining159 captions. On insufficient
resources, `tools.run_gpu_group` waits; occupied GPUs and other jobs are not
interrupted. All environments, workdirs, weights, logs, caches and temporary
directories stay on the cloud workspace. Use short `.pwtmp/h3p` for IPC.

`tools.watch_minimax_phygen` orchestrates generation and the existing
`tools.evaluate_phygen_three_stage_gpt55` with32 judge workers. Generation state
is `baselines/minimax_h3_phygen`; scores/call records are in
`evaluation/minimax_h3_phygen_gpt55_three_stage_mcq_v1`. This namespace preserves
the completed800 Seedance/coding-agent judgments and the PCVE calibration.

Judge version:`phygen-gpt55-three-stage-mcq-v1`. Identical question text,
chronological frame selection, RGB/longest-edge768/JPEG85 processing and
aggregation are used for all compared models. Ordinal MCQ substitution is
explicit; scores are not claimed identical to the original open-VLM metric.
The published MiniMax score0.61 remains a reference until a matched local
full160 result is available.

After the A800 worker finishes its bounded PCVE backfill,
`tools.watch_minimax_phygen_backfill` can run disjoint private T2V tail batches.
Selection excludes completed/active prompts and requires a conservative time
margin ahead of the original generator. Private batch output is isolated with
`--output-root`; its generation lock does not replace the original full-run
lock. Only verified120-frame24FPS clips are imported, and a fresh prefix check
requires two pending cases before the imported case. Existing active/accepted
outputs are never overwritten. Sampling and official caption mapping stay the
same; the original GPU generator continues without interruption.
