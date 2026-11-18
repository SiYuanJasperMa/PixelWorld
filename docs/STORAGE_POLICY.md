# Experiment storage and recovery

Agent workspaces, submitted tensors, frames, candidate histories, full traces,
evaluation results, rendering caches and publication checkouts belong on shared
BN storage. A path under `/mnt/bn` must also resolve there: a symlink to a local
disk does not satisfy this policy for permanent artifacts.

The current API proxy logging mode uses a bounded asynchronous queue and writes
proxy diagnostics directly to cloud storage. The native Codex archive is the
primary full trajectory record; an interrupted proxy writer reports its pending
state and cannot claim that duplicate diagnostics are complete. A separate
worker compresses closed records, reads them back, checks SHA256, and only then
removes unchanged sources. In-flight records are never pruned.

For this mode, use `--async-cloud-logs` with a cloud log directory. Do not also
enable the old `--spool-max-bytes` admission mechanism: it tracks durable spool
files but does not account for the asynchronous writer's pending cloud records.
An observed 3.7 MiB request was rejected while writing its small active marker
because both mechanisms were enabled. This is an infrastructure configuration
failure, not a video or context-budget failure. The isolated Flash recovery
retains the existing 256 MiB queue and 128 MiB request limit; it changes no model
input, pixel task, output budget or official evaluator.

Historical synchronous durable-spool configurations remain supported separately.
They reserve request/response capacity before upstream sampling, cap the local
spool at 2 GiB, and require 20 GiB free root-disk space. Their independent worker
archives and verifies bytes on cloud storage before reclamation. Do not change a
live generation route by restarting its busy proxy; validate the isolated route
first and arrange any later restart at an idle boundary.

Storage backpressure and positively recorded `No space left on device` errors
are infrastructure interruptions, excluded from the five-model-attempt limit.
`tools/annotate_storage_incident.py` preserves the original manifest before
annotating existing records. Successful submissions remain unchanged.

For a frozen old spool, `tools/retire_verified_spool.py` checks the independently
verified full-snapshot receipt, archives and verifies all added/changed files,
then releases exactly those source files. It refuses a live writer, an
unverified snapshot, or source changes during recovery. The full snapshot,
delta receipts and original case traces remain available on BN for analysis.

Verification covers concurrent admission reservations, byte-exact recording,
oversized-record rejection, compressed archive readback, active/changed-file
preservation and infrastructure-attempt accounting. Operational acceptance also
checks the actual root free space, archival heartbeat, publication checkout
location and public model-progress/video pages.

Native video exports share one large-media slot. Already published immutable
assets and candidate metadata are reused after a checkout migration instead of
decoding every historical tensor again. Check the container cgroup memory limit,
not host `free` output, before choosing generation concurrency. On the current
32 GiB container, 720p generation uses six slots and 4K generation uses two;
ongoing cases remain alive while a matrix controller is reloaded.

Large-video web previews retain the native dimensions, FPS and duration but use
H.264 compression with an 18 Mbps maximum rate. The original lossless RGB tensor,
PNGs and traces remain on BN; evaluation never uses the web preview. Small RGB
players and the already verified standalone 4K pixel smoke remain unchanged.
Publication stages only changed paths, so an unrelated historical media backlog
does not require a complete working-tree scan on every progress update.
