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

Closed API records can move between explicitly registered storage roots while
run archival is in progress. The archiver resolves each exact response ID and
record role in those roots, verifies the recorded bytes, and rejects conflicting
copies. A missing old path alone does not prove that the original message is
lost. Register the destination root before migrating closed records, and retain
the verified migration receipt before removing the source copy.

A verified run archive is reused when relocation changes only file location or
filesystem identity. Changed members are checked against the existing archive's
SHA256 records and a relocation proof is saved separately; the verified archive
and receipt remain unchanged. Changed content, configuration or run manifests
still require their own archive revision. An unfinished temporary archive never
authorizes source deletion. Verify coverage across all retained experiment
registries and earlier backup formats; a recent-window archive report alone is
not a complete paper-record inventory.

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

Blocked admissions share a full reconciliation at most once every 30 seconds.
They continue checking reservations between scans, so completed requests can
write and release their leases. Repeating a full directory scan for every
waiting request holds the writer lock and can prevent a full segment from
draining. An observed directory timestamp change still reconciles immediately;
when a filesystem reuses the timestamp, the bounded scan recovers the change.
Explicit fresh inspection remains available. Capacity denial starts no upstream
sampling and does not remove any original records.

A segment controller can create and fsync the `.spool_drain` marker in a live
log root before rotating it. Readiness then reports `draining=true` and rejects
new admissions before upstream sampling, while already leased requests can
finish writing and release their reservations. Waiting admissions also observe
the marker. Rotate only after the reported active reservation count reaches
zero; retain the closed segment and use a fresh directory. This prevents a
steady stream of small requests from occupying the last available slot forever.
A legacy process without the `draining` readiness field does not support this
control and must first reach its ordinary idle boundary.

Draining admissions keep the same bounded wait as a full buffer (60 seconds
by default), without leasing capacity or sampling upstream. Returning an error
immediately can exhaust CLI reconnect retries before leased requests finish.
The wait releases the writer lock between checks; existing responses still
finish durably, and the client can reconnect to the fresh segment after rotation.

Codex stdout capture also waits through quota, full-disk, read-only-filesystem
and I/O errors. It retains the current event in memory until both the original
and annotated copies are fsynced. A retry rolls back only that event's partial
writes, preserving earlier records and its original receive timestamp. The
agent's pipe provides backpressure while storage recovers. The run waits for
capture to finish after the CLI exits; it cannot discard final events after a
fixed join timeout. Capture recovery is recorded in `logs/stream_capture_state.json`.
An unrecoverable capture failure is exposed to the supervisor, which stops its
own agent and records an infrastructure failure rather than leaving an orphan.

Static path checks distinguish Python arithmetic, including versioned
interpreters such as `python3.11`, from filenames. A bounded, name-only
`find` for Python interpreters may inspect the parent of explicitly granted
tool directories. It does not grant access to files beneath that parent:
content reads, find actions and content-reading pipelines remain blocked.
Closed deliveries rejected by an earlier false path flag can be revalidated
with `tools.revalidate_audit_failures`, which backs up the original manifest
and checks that generated code and pixel hashes remain unchanged.

A missing `convert` executable is a warning only for the observed standalone
literal crop of `first_frame.png`; it cannot create its scratch output. An
uncaught Pillow save error at a verified top-level stdin line also proves that
a later unique literal top-level save did not run. Earlier reads, existing
files, repeated path uses, exception handlers and compound commands remain
outside this exception. These checks do not grant host `/tmp` access.

The evidenced ffmpeg crop denial applies to a literal crop of `first_frame.png`
with or without a following literal scale. Other inputs, filters and shell
actions retain the read/write boundary. A codec's generic "Input/output error"
for an unopenable `/tmp` crop is not proof that the cloud volume is full.

Recovery candidate selection follows the existing task-specific first-frame
contract. I2V requires an exact first frame. T2V and V2V report `frame0_exact`
as null and use `first_frame_constraint_satisfied`; null alone must not prevent
review of their closed artifacts. Recovery still validates every required frame,
tensor, code hash, media setting and access boundary before restoring a delivery.

Literal `PATH`, `PYTHONPATH` and `LD_LIBRARY_PATH` assignments are checked as
lists of directories. Every component must already be readable; a colon does
not turn two granted library directories into an outside filename. Actual file
operands with colons remain subject to path checks. The bounded interpreter
lookup also recognizes the observed name-only Python/OpenCV package query;
find actions and content-reading pipelines remain outside this exception.

Completed bridge requests release their capacity reservation in a final cleanup
guard, even if removing the transient active marker fails with quota or I/O
errors. A failed marker removal leaves diagnostics intact; original request and
response files are not deleted. An unreleased reservation must not be mistaken
for proof that upstream sampling is still active.
