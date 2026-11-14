# Experiment storage and recovery

Agent workspaces, submitted tensors, frames, candidate histories, full traces,
evaluation results, rendering caches and publication checkouts belong on shared
BN storage. A path under `/mnt/bn` must also resolve there: a symlink to a local
disk does not satisfy this policy for permanent artifacts.

The API proxy may use a **2 GiB maximum local durable spool**, with **20 GiB
minimum free root-disk space**. Admission reserves capacity for both request
encodings and output/event logs before contacting the upstream provider. An
independent worker gzip-compresses each record, reads it back from BN, checks
SHA256, and only then removes an unchanged, closed source record. In-flight
requests and the current request-summary ledger are never pruned; the summary
ledger rotates at 8 MiB. Credentials stay private and are excluded from this
continuous archive. These controls do not change agent prompts or token budgets.

Start the proxy with `--spool-max-bytes 2147483648 --min-free-bytes 21474836480`.
Start `tools/spool_api_logs.py watch` with `--prune-verified --max-bytes 2147483648`.
Point its `--archive` and the live publisher's `--checkout` to BN directories.
The watchdog specification is the authority for service paths and restarts.

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
