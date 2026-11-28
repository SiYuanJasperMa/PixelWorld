# Codex filesystem isolation

New formal configurations select `"filesystem_policy": "case-only-v1"`.
The native Codex 0.169.0 permission profile grants read/write access to the
current case workspace and read access to the required Python libraries and
runtime executable directories. `:minimal` supplies the standard Linux sandbox
mounts. Direct tool networking is disabled.

Other run directories, the evaluator, references, scores, API keys, and the
Codex session/archive directory are outside the declared readable roots. The
Codex client retains its own API connection and external session archive; those
are not inherited by the shell tool environment.

The runner writes `permissions_profile.json` outside the agent workspace and
records its hash, the filesystem policy, and the exact command in
`run_manifest.json`. Paths are resolved before building the profile. The profile
includes executable/library directories, not the whole runtime directory, so
sibling credentials and API diagnostics do not become readable.

Configuration preparation through `tools/final_experiments.py` selects this
policy. Existing configurations without the field preserve `workspace-write`;
that older policy limits writes but does not provide workspace-only reads.
Legacy protocols and Vision-Input cannot silently select the new policy.
Changing the execution boundary does not change pinned evaluators or datasets.

Validate positive and negative controls with real native tools: Python imports,
workspace reads/writes, reading the agent's own image, outside absolute and
relative paths, symlink escapes, and an outside image. An outside path becoming
invisible is a valid denial only when it exists in the host control. Synthetic
parent directories in the sandbox may be writable; verify an outside write
against the real host filesystem before calling it an escape.

The pinned CLI's `:minimal` support is necessary for system symlinks and device
mounts. Explicit writable device-file roots or `/dev` overrides interfere with
protected-subpath handling and the image read helper; do not add them.

Run `pytest -q`, `pixelworld doctor --config CONFIG --require-sandbox`, and a
single real generation smoke before applying the policy to a matrix. A config
being accepted is not evidence that every tool or dependency works. API usage
and output artifacts remain recorded by the external harness.
# Temporary files

The standard case-only profile grants writable scratch storage at workspace
`.tmp/`; the runner sets `TMPDIR` and library cache paths there. It does not
grant the host's shared `/tmp`. Creating an empty host directory alone does not
make it private: an independent mount or namespace is needed before `/tmp`
could safely become an allowed scratch path.

`tools.verify_case_isolation` checks writable workspace scratch, blocked shared
`/tmp` writes, blocked neighbor reads and symlink escapes, and disabled direct
network access with the pinned Codex sandbox. The audit accepts workspace
`.tmp` paths. Proven failed writes to nonexistent `/tmp` scratch targets are
warnings; successful outside reads remain violations. Historical permissions
are preserved when revalidating old outputs.

When a literal crop cannot open its first scratch output, subsequent crops and
a final `ls -l` of those same outputs behind `&&` are recorded as failed scratch
operations. The denial must identify the first output; other paths, commands,
successful writes and shell operators retain their existing checks.

## Recovering audit-only rejections

Recovery verifies unchanged code, RGB data and output contracts, and saves the
original failed manifest and validation report. A quota or read-only failure
defers only its model/track/case. Other cases keep their existing retry policy;
a valid candidate waiting for audit recovery is not generated again.

A small evidence-write probe catches storage failures before repeated whole-video
decoding. Recovery reports the actual errno and filename rather than stopping an
entire matrix when one record cannot be updated.

For a verified storage copy, recorded paths retain their original execution
meaning. Recovery can map exact original workspace aliases and literal parent
paths that resolve inside that original workspace to the copy. The original
manifest, code, RGB data, event stream and permission profile must match first.
Raw events stay unchanged; the audit projection and its hashes are stored
separately. Other experiments, references and scores remain outside the allowed
workspace. Directory changes with unresolved variables are not guessed.

These are retrospective validation corrections. They do not change the agent's
filesystem or network isolation, generation prompts, or evaluator/dataset pins.

The static command audit recognises division and slice syntax in literal Python
heredocs and `python -c` arguments. A slice such as `frame[y-h//2:y+h//2]` does
not name an absolute file. Quoted filenames remain checked, and shell-expanded
or unparseable arguments do not receive this arithmetic exemption.

A writable host may record that an exact closed failure is ineligible for audit
recovery. Retry controllers accept this review only when the original manifest,
audit implementation and recovery-tool hashes match. The record remains failed;
the review releases a storage deferral so an already authorized fresh attempt can
run through normal validation. A stale review cannot release the deferral, and
the review never turns a rejected video into a successful result.
