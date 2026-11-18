# Retry accounting

The default cap is five total model attempts per case. Token budget exhaustion,
ending without a submission and an invalid submission consume a model attempt.
Only positively identified infrastructure interruptions are exempt: for example,
a recorded provider queue/connection error or `validation.infra_error`. Unknown
failures are counted conservatively rather than allowing unbounded retries.

Run directory ordinals are permanent record IDs. Two infrastructure records
followed by five model attempts can therefore produce seven directories. All
existing directories, successful submissions, token usage and retry ledgers are
retained. A new authorization starts after the highest existing directory ID;
there is no renumbering or moving failure records to make room for retries.

`--max-total-attempts 5` controls model attempts directly. It is not translated
into three authorization rounds. `--max-rounds` remains a legacy explicit option
and has no default effect. The watcher preserves the existing token budget and
its recorded authorization-window accounting.

Historical attempts already authorized under earlier limits remain available.
Lowering a cap prevents new launches after the model-attempt limit is reached;
it does not hide a previously validated successful submission.

## Scoped near-complete rescues

The owner has authorized extra attempts for a sparse failed tail of an otherwise
complete cohort. `--near-complete-extra-attempts 2` enables this policy explicitly:
at least 95% of cases must be delivered, at most three may remain terminal, and
no ordinary attempts or infrastructure retries may still be pending/running.
The authoritative case inventory is rechecked before granting a rescue.

Each authorization grants one fresh attempt to that case only, with at most two
extra real attempts beyond the default cap. The external ledger records the
scoped `model_attempt_cap`, cohort coverage, original attempt history and reason.
It does not modify the global cap or supply evaluator feedback. A completed
rescue is marked with an asterisk in public progress and keeps its source hash.
Report default-cap delivery and final rescued delivery separately in analysis.
