# Infrastructure audit and regression checks

The filesystem permission profile is the execution boundary. Delivery validation
checks RGB/media artifacts, regenerated code and logged behavior. Command-text
auditing is additional evidence; a literal parent reference is not itself proof
of an outside read.

## Parent-path correction

The old rule marked any `../` command fragment as outside-workspace traversal.
From a case subdirectory, `../first_frame.png` can resolve to the allowed input.
The current audit resolves literal parent references against an explicitly
recorded working directory or a leading literal `cd ... &&`. Resolving outside
the case or through a symlink to an external file remains blocked. Dynamic or
untracked directory changes remain conservative; do not claim that lexical
auditing proves an actual successful escape. Original manifests and traces are
preserved. New smoke uses a recorded code snapshot with the corrected audit.

Regression tests cover internal relative reads, explicit directories, symlink
escapes, real parent escapes and unknown directory changes.

## Native sandbox probe

`tools.verify_case_isolation` uses the actual pinned native Codex sandbox on the
generation worker, with the same generated case-only permission profile. It
creates harmless synthetic canaries and records these independent checks:

- internal read from a subdirectory works;
- absolute and relative reads of a neighboring file are denied;
- a case-local symlink to the neighboring file cannot read it;
- synthetic external filenames are absent from the virtual parent listing;
- direct network access is denied, including socket creation where applicable.

The virtual parent directory may be listable because mount ancestors must exist.
This is not permission to read neighboring experiments. The generated report
records the actual visible entries and errno values. No credentials or benchmark
ground truth are used in this test.

## Remote progress and recovery

Local `/proc` is not a liveness check for a remote PID. The initialization analysis
uses fresh worker-owned matrix status to identify running and terminal cases.
A stale or missing remote status is infrastructure uncertainty; a running
manifest must not be silently reclassified as a model delivery failure.
The paired-analysis regression tests cover this distinction and preserve
validated completed deliveries.

Generation, review, evaluation, HF media upload and frontend publication have
separate services. Candidate selection is frozen before downstream video scores.
HF smoke uploads are bounded batches: the first small progress snapshot does
not wait for all ten baseline videos and traces. Links are exposed only when the
corresponding assets are included in an acknowledged or atomic publication.

## Attempt, resource and storage policy

Real generation failures, including output-budget exhaustion, use the normal
five-attempt cap; infrastructure failures remain separate. Only user-authorized
nearly completed cohorts receive additional recovery attempts. Keep the existing
model/global concurrency controls and cgroup memory-headroom gate. API queueing
is an upstream wait, not a reason to kill a live generating case.

Workspaces, inputs, environments, caches, scratch paths, logs and publication
state remain on cloud storage. GPU SSH workers provide computation and use the
same cloud repository. Select a device without a real compute task; yielding
keepalive processes are distinct from evaluation workloads. Do not terminate
unrelated GPU jobs.

Required harness verification is `pytest -q` and `pixelworld doctor`; new matrices
begin with a case smoke. The corrected path audit passed the full 523-test suite
and doctor with Codex 0.169.0 before smoke launch. Additional remote-status
regressions have their own recorded results. Evaluator commits and dataset
revision pins are unchanged. Actual acceptance results remain in generated
reports; this document is a procedure and scope description, not a substitute
for those reports.
