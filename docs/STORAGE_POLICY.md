# Experiment storage and recovery

Agent workspaces, submitted tensors, frames, candidate histories, full traces,
evaluation results, rendering caches and publication checkouts belong on shared
BN storage. A path under `/mnt/bn` must also resolve there: a symlink to a local
disk does not satisfy this policy for permanent artifacts.

All standard TRAE Responses bridge launches use the same model-independent
storage defaults: **64 GiB per proxy log directory** and **20 GiB of filesystem
free space reserved**. These defaults are defined once in
`pixelworld/log_storage.py`; omitting launcher flags still applies them. A log
directory can serve many runs. This is neither a per-run memory allocation nor
an input/output token limit. Old 2 GiB launcher overrides must not be carried
into new standard services.

The standard synchronous writer reserves request/response capacity before
upstream sampling and records original API inputs, native requests, events and
responses on cloud storage. Completed files occupy the directory budget until
verified reclamation. Full native CLI trajectories and per-run verified archives
are retained as well. A separate worker compresses closed records, reads them
back, checks SHA256, and only then removes unchanged sources. In-flight records
are never pruned.

Check `/ready` before dispatch: HTTP 503 indicates insufficient durable log
admission capacity. `/health` proves the listener is alive and may still return
HTTP 200 with `ready=false`. A capacity snapshot is not a reservation or proof
of upstream model availability. Read back a real API request and its usage after
startup. A running older process does not acquire new defaults merely because
source files changed; record its effective configuration and update it at an
idle boundary after isolated validation.

Explicit legacy `--async-cloud-logs` remains supported with its separate bounded
queue. The CLI rejects combining this mode with durable spool admission: the
spool does not account for pending asynchronous cloud records. An interrupted
legacy writer reports its pending state and cannot claim the raw diagnostics
are complete. Historical modes and receipts remain available; the standard
launch policy uses synchronous durable logging.

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

## Durable log accounting

Each mutable proxy log directory has one writer, enforced with an advisory
file lease. The writer counts existing files once and updates the byte ledger
for each durable write and transient-marker deletion. Request admission and
HTTP health checks use that ledger instead of scanning every historical file.
The64GiB capacity,20GiB free-space reserve, request reservations, atomic
replacement and fsync remain enforced.

External archival may remove verified immutable closed records. The cached
ledger then overestimates usage until a fresh capacity inspection or admission
reconciliation at the capacity boundary. Do not append or restore files into
a live leased directory; stop its writer first, or explicitly reconcile before
resuming admission. `capacity_status()` performs a fresh inspection by default;
`refresh=False` is the single-writer runtime snapshot.
