# Benchmark service recovery

Use [EXPERIMENT_RUNBOOK.md](EXPERIMENT_RUNBOOK.md), then the owner's local
handoff for exact paths and current pause decisions.

```bash
python -m tools.watch_benchmark_services --spec "$SERVICE_SPEC"
```

The local specification records commands, cwd, PID receipts, logs, identities,
dependencies and optional stop markers. Credentials come from private files.
Choose only authorized services: an old combined specification may include
scoring or retired routes. Keep specifications outside Git and agent workspaces.

The watchdog checks PID identity, excludes zombies and uses a single-instance
lock. After reboot, inspect actual owners before restarting. Verify CPU-owned
work on the CPU worker. Detached services require an explicit restart or a host
process manager after a reboot.

Use registered continuation configs and `tools.watch_failed_retries`. Five real
attempts are the general allowance; RBench's original allowance is one. Infra
records are exempt. Owner-authorized recovery is separate and stops at first
valid delivery. Preserve history and accepted-case reuse.

Model proxies use 64 GiB log capacity and a 20 GiB filesystem reserve. Test real
durable writes because network-filesystem free space can disagree with quota.
Check `/ready`, forwarding and response records; `/health` alone is insufficient.
Preserve raw request/response bodies when relocating a cloud spool.

The publication chain exports sanitized records, uploads cases/media/traces,
updates HF progress and publishes the static HF Space. It is independent of
generation and scoring. See [HF_PUBLISHING.md](HF_PUBLISHING.md).

The original guide is retained in
[the archive](archive/2026-11/SERVICE_RECOVERY_PRE_20261127.md).
