# Provider and infrastructure recovery

Keep generation prompts, media settings, model identity, and the case budget fixed when diagnosing a failed submission. Inspect the original API request and response, not only the CLI's `turn.completed` event.

The cumulative case output budget and a provider's per-response output limit are different controls. Standard bridges use a shared fallback of 128,000 output tokens per response; explicit client limits remain unchanged. A gateway can truncate one reasoning response even when the agent has substantial case budget remaining. The Responses bridge preserves native `length` as `response.incomplete` with reason `max_output_tokens`; it must never label this response completed or mark a partial tool call complete. An explicit client output limit is retained.

Adjacent parallel tool calls are serialized as one assistant tool batch, followed by their original result messages and IDs. This normalizes the transport without changing the task prompt or requiring extra generation or inspection.

The reproducible TRAE bridge is available in the repository:

```bash
python -m pixelworld.traex_responses_proxy \
  --model-catalog "$PIXELWORLD_MODEL_CATALOG" \
  --auth-file "$PIXELWORLD_PROVIDER_AUTH_FILE" \
  --log-dir "$PIXELWORLD_PROXY_LOG_DIR" \
  --port 9593 \
  --default-max-output-tokens 128000 \
  --lossless-input-images \
  --spool-max-bytes 68719476736 \
  --min-free-bytes 21474836480 \
  --queue-retry-delay 30
```

Supply these paths privately. Credentials, model-catalog snapshots, raw API payloads, and run artifacts belong in cloud storage and must not be committed. Configure the fallback for the actual provider's capabilities; accepting the parameter in a short API probe does not prove a long response can reach that limit. Lossless input tiling addresses per-image byte limits without resizing or removing pixels.

All standard bridge launches retain up to 64 GiB of durable logs and reserve 20 GiB of free disk space by default, including when these flags are omitted. The constants are shared across models in `pixelworld/log_storage.py`. This directory budget includes completed request logs; releasing an active request reservation does not remove its input/output files. A small directory budget can block inference even when the filesystem has free space. Copy and independently verify records before reclaiming closed logs.

Check `/ready` before dispatching: it returns HTTP 503 when durable storage cannot admit a request of the configured maximum size. `/health` remains a liveness check and can return HTTP 200 with `ready=false`. These capacity checks are snapshots and do not reserve a slot or prove upstream availability. Follow them with a real admission probe and the unchanged one-case smoke; preserve and exempt explicit storage rejections as infrastructure failures.

GLM-5.4's official model page advertises a 128K maximum output. The inspected gateway route still ended a response at 32,000 reasoning tokens after receiving `max_tokens=128000`, `reasoning_effort=high`, and the non-preset selector. This establishes an observed route limit, not its cause. Keep deployment limits, upstream parameter handling, and any intrinsic thinking cap unresolved until there is direct evidence; do not label all such truncations infrastructure failures. An adapter that reports native `length` as normal completion is a separate verified defect.

A separate live control requested `max_tokens=1` with the non-preset selector and received 216 output tokens, including 17 reasoning tokens. The inspected native request retained the value `1`. Thus this route does not enforce that request field as an output ceiling; a successful HTTP response does not establish parameter support. This does not identify the upstream component responsible or prove that the 32,000-token stop has the same cause. Verify effective parameter behavior before changing a production configuration.

Some backends still apply a smaller limit despite the requested value. Keep these provider limits separate from voluntary model completion and from an adapter that incorrectly claims completion. The optional `--use-request-parameters` flag changes the native preset selector; it requires an unchanged one-case smoke and is not a general solution to backend limits. Provide the standard shell tools, including `rg`, in the frozen runtime's permitted `bin` directory rather than relying on an unrelated host PATH.

Recovery requires positive evidence:

- A queue, connection, storage, authentication, or hidden-runtime failure is an infrastructure failure. Fix its cause and retry without spending a model attempt.
- `tools/reclassify_output_limit_failures.py` can recover an audited proxy-fallback truncation. It verifies the final response ID, native stop reason, requested limit, usage, and case budget before exempting the attempt.
- `tools/reclassify_provider_infrastructure.py --model MODEL_KEY` restricts provider-error recovery to an explicit model. The sandbox recovery tool requires a recorded executable error and the matching permission profile.
- A native `stop` with no submitted artifacts is a model delivery failure when the required tools worked and the adapter preserved its output. Missing artifacts or missing usage alone do not prove an infrastructure failure. An optional missing command must be investigated separately.

Reclassification preserves the original manifest byte for byte, its hash, the original API evidence, and the failed status. Do not move or renumber old attempts. The supervisor counts real model attempts separately from infrastructure attempts. Standard retries remain capped; an owner's explicit until-first-delivery authorization is a separately recorded exception for the selected model.

Before deploying a gateway or harness change, run `pytest -q`, `pixelworld doctor`, and one case with the unchanged benchmark configuration. Drain or preserve requests already in flight when replacing a dispatcher. Register new producer paths with the dashboard and archive watcher so restored runs and their raw API records remain visible.

For a gateway cutover, retain both original log directories. The registered run archiver accepts `--proxy-fallback BASE_URL=RETAINED_LOG_ROOT` alongside the current `--proxy-route`. It combines original records by exact response ID, refuses conflicting bytes, and independently verifies each numbered run archive. An unchanged source snapshot remains reusable across the cutover; no raw source is deleted by this option.

Static audit recovery uses `tools.revalidate_audit_failures`: it accepts only a closed successful CLI turn whose saved RGB artifacts pass all current output and access checks. Python division in appended or compound heredocs and sed syntax delimiters are not file paths. The original manifest and validation are preserved; generated code and tensors are hash-checked and unchanged. `watch_failed_retries --audit-revalidate` checks these artifacts before authorizing regeneration, deferring to a writable host when a historical alias is read-only. Verify a cloud runtime snapshot SHA on the compute worker before launching a matrix; updating files does not prove that a running or cached process uses the new implementation.
