# Provider and infrastructure recovery

Keep generation prompts, media settings, model identity, and the case budget fixed when diagnosing a failed submission. Inspect the original API request and response, not only the CLI's `turn.completed` event.

The cumulative case output budget and a provider's per-response output limit are different controls. A gateway can truncate one reasoning response even when the agent has substantial case budget remaining. The Responses bridge preserves native `length` as `response.incomplete` with reason `max_output_tokens`; it must never label this response completed or mark a partial tool call complete. An explicit client output limit is retained.

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
  --queue-retry-delay 30
```

Supply these paths privately. Credentials, model-catalog snapshots, raw API payloads, and run artifacts belong in cloud storage and must not be committed. Configure the fallback for the actual provider's capabilities; accepting the parameter in a short API probe does not prove a long response can reach that limit. Lossless input tiling addresses per-image byte limits without resizing or removing pixels.

Some backends still apply a smaller limit despite the requested value. Keep these provider limits separate from voluntary model completion and from an adapter that incorrectly claims completion. The optional `--use-request-parameters` flag changes the native preset selector; it requires an unchanged one-case smoke and is not a general solution to backend limits. Provide the standard shell tools, including `rg`, in the frozen runtime's permitted `bin` directory rather than relying on an unrelated host PATH.

Recovery requires positive evidence:

- A queue, connection, storage, authentication, or hidden-runtime failure is an infrastructure failure. Fix its cause and retry without spending a model attempt.
- `tools/reclassify_output_limit_failures.py` can recover an audited proxy-fallback truncation. It verifies the final response ID, native stop reason, requested limit, usage, and case budget before exempting the attempt.
- `tools/reclassify_provider_infrastructure.py --model MODEL_KEY` restricts provider-error recovery to an explicit model. The sandbox recovery tool requires a recorded executable error and the matching permission profile.
- A native `stop` with no submitted artifacts is a model delivery failure when the required tools worked and the adapter preserved its output. Missing artifacts or missing usage alone do not prove an infrastructure failure. An optional missing command must be investigated separately.

Reclassification preserves the original manifest byte for byte, its hash, the original API evidence, and the failed status. Do not move or renumber old attempts. The supervisor counts real model attempts separately from infrastructure attempts. Standard retries remain capped; an owner's explicit until-first-delivery authorization is a separately recorded exception for the selected model.

Before deploying a gateway or harness change, run `pytest -q`, `pixelworld doctor`, and one case with the unchanged benchmark configuration. Drain or preserve requests already in flight when replacing a dispatcher. Register new producer paths with the dashboard and archive watcher so restored runs and their raw API records remain visible.
