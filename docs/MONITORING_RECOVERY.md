# Monitor and recover the current experiments

Start with [the experiment runbook](EXPERIMENT_RUNBOOK.md). The owner's
repository-root `EXPERIMENT_HANDOFF.local.md` and `runtime.local.json` supply
exact paths, worker addresses and current pause decisions.

```bash
python -m tools.ops.status --map runtime.local.json
# Execute on the CPU worker to verify its recorded owners:
python -m tools.ops.status --map runtime.local.json --role cpu --verify-processes
```

Snapshot counts, live case processes, successful API responses and video
deliveries are separate observations. Check PIDs on the owning host; a heartbeat
or a saved PID does not prove inference. Failed observations remain unknown.

The current suite is Physics-IQ Verified, RBench Common Manipulation,
PhyGenBench and PCVE. Native GPT-6.2 continuation and API recovery have separate
registered controllers. Read pause decisions before starting generation,
scoring, publication or harness comparisons. RoboJudge, Flash, Pi and discarded
initialization studies are historical.

Recovery preserves compatible successes, failures and exact contracts. Use the
frozen Python/Codex runtime. Verify durable cloud writes, forwarding, a model
response and a valid delivery. Queueing is a provider wait; quota exhaustion,
broken forwarding and a stuck log writer are infrastructure problems.

`tools.watch_benchmark_services` supervises only an explicitly selected service
specification. Detached processes survive shell closure, not a reboot. Check
owners before restarting and keep one owner per matrix/ledger. See
[service recovery](SERVICE_RECOVERY.md).

The HF exporter, case uploader, progress uploader and main/analysis publishers
are separate services. Validate public JSON timestamps and video/detail links.
[HF publishing](HF_PUBLISHING.md) is the current route; GitHub Pages is historical.

The old guide is preserved in
[the archive](archive/2026-11/MONITORING_RECOVERY_PRE_20261127.md).
