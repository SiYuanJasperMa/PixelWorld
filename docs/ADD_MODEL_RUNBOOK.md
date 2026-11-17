# Add a new model

This runbook is for collaborators who already have their own Codex
installation and model/API access. PixelWorld does not vendor Codex or API
keys; it launches the user's `codex` executable in clean per-case workspaces.

## 1. Install and verify Codex

Use Codex CLI **0.169.0** for the frozen main experiment. Do not silently
upgrade within a cohort. Another version requires a separately named protocol
and a new smoke; it cannot be pooled into the current main table.

```bash
codex --version
export PIXELWORLD_CODEX="$(command -v codex)"
python -m pixelworld.cli doctor --config configs/benchmark.example.json
```

If your model is available in the local Codex catalog, use a `builtin`
provider. If it is exposed through an OpenAI Responses-compatible gateway, use
`provider.type="responses"`. PixelWorld passes the environment-variable name to
Codex; never put secrets in JSON.

```bash
export MY_MODEL_API_KEY='...'
```

```json
{
  "key": "my-model",
  "model": "provider/model-id",
  "reasoning_effort": "high",
  "cli_backend": "codex",
  "tracks": ["vision-agent"],
  "provider": {
    "type": "responses",
    "id": "my_gateway",
    "name": "My Responses gateway",
    "base_url": "https://api.example.com/v1",
    "env_key": "MY_MODEL_API_KEY"
  }
}
```

For Kimi, GLM, Qwen or another collaborator model, use the provider's exact
versioned API model ID. Do not replace a requested version by a family alias.
The model may use a built-in Codex provider or an explicit Responses-compatible
provider; other wire protocols need a Codex-compatible adapter first. Do not
change benchmark instructions to make a specific model submit successfully.

## 2. Choose the benchmark config

Start from the closest template:

| Benchmark | Template | Notes |
|---|---|---|
| Physics-IQ Verified | frozen 720p adapter config | 198 I2V cases; 1280x720, 24 FPS, 120 frames / 5 s. |
| RoboJudge shared-50 | frozen 720-short-edge adapter config | 50 I2V cases; aspect preserved, 24 FPS, 120 frames / 5 s. |
| PCVE-RigidBench | frozen standard adapter config | 129 video-editing cases; source-native 1280x720, 24 FPS, 96 frames / 4 s. |
| PhyGenBench T2V | frozen photographic pixel-space adapter config | 160 official prompts; 1280x720, 24 FPS, 120 frames / 5 s. |

Use a new output root for every new model/batch. Do not write into another
model's completed directory.

```json
{
  "runs_root": "runs/my_benchmark_my_model_YYYYMMDD",
  "max_concurrency": 2,
  "max_per_config": 1,
  "max_attempts": 2,
  "max_total_attempts": 5,
  "output_token_budget": 100000
}
```

The public policy is at most **5 total model attempts** per case.
`token_budget_exhausted` counts as a model failed attempt. Infrastructure
failures such as missing keys, 429/rate limits, provider payload errors, mux
errors or stream transport failures should be quarantined and retried without
counting as model attempts.

## 3. Run a smoke

Run one representative case in the main `vision-agent` track before launching
the full matrix. The paper-facing name is **Vision**. Text is a separate
analysis condition and is not required for a new main-table model.

```bash
python -m pixelworld.cli run-case \
  --config configs/my-model.local.json \
  --model my-model \
  --track vision-agent \
  --case 0001
```

Inspect:

- `run_manifest.json`: validation pass/fail, token usage, stop reason;
- `workspace/generate.py`: rerunnable generator;
- `workspace/pixels.npz`: RGB tensor;
- `workspace/index.html`: local playback page;
- `logs/events.jsonl` and `logs/events.raw.jsonl`: actual Codex events and
  tool evidence.

## 4. Launch the full matrix

```bash
python -m pixelworld.cli matrix run --config configs/my-model.local.json
python -m pixelworld.cli matrix status --config configs/my-model.local.json
```

For durable background operation, use the service/watchdog pattern under your
own run root. The current long-running run uses:

```text
runs/bench_codex_20261109/benchmark_services.json
tools/watch_benchmark_services.py
tools/watch_failed_retries.py --max-total-attempts 5
```

The recommended portable entrypoint is `tools/final_experiments.py`. Copy
`configs/collaborator-model.example.json` to an ignored local roster and replace
only its exact model/provider fields. The frozen benchmark input
config root is distributed separately because licensed benchmark inputs are not
stored in Git.

```bash
cp configs/collaborator-model.example.json configs/my-model-roster.local.json
# Set one exact model/version; do not put the API key in this file.
python tools/final_experiments.py prepare \
  --input-config-root /path/to/frozen-720p-adapter-configs \
  --bundle runs/final_my_model_YYYYMMDD \
  --roster configs/my-model-roster.local.json

pixelworld doctor \
  --config runs/final_my_model_YYYYMMDD/configs/physics_iq_smoke.json
python tools/final_experiments.py start \
  --bundle runs/final_my_model_YYYYMMDD
```

Review all four smoke deliveries before enabling the reviewed full launch. A
collaborator should not edit `READINESS.json` to bypass a failing runtime,
filesystem or usage-accounting check.

After those generation gates are recorded as passed, the full Vision matrix is:

```bash
python tools/final_experiments.py start \
  --bundle runs/final_my_model_YYYYMMDD \
  --full --review-approved --generation-only
```

## 5. Evaluate

Only evaluate complete cohorts:

- Physics-IQ: all 198 valid videos for one model/track;
- RoboJudge: all 50 valid videos for one model/track;
- PCVE-RigidBench: all 129 valid videos for one model/track;
- PhyGenBench T2V: all 160 official prompts for one model/track.

Missing videos block official scoring. Do not duplicate outputs, drop failed
cases or invent zero-score rows unless a separate protocol explicitly says so.

## 6. Publish monitoring pages

Raw run directories are large and should not be committed to the source repo.
The repository should contain code, configs, docs and small provenance files
only.

For the live project site:

- source repository: `SiyuanMaCS/PixelWorld`;
- public site branch/repo path: `SiyuanMaCS.github.io/pixelworld/`;
- public entry: <https://siyuanmacs.github.io/pixelworld/index.html>.

Use `tools/sync_stable_pixelworld_pages.py` to publish dashboards and bounded
case previews. Large generated videos/tensors stay in the run root or an
artifact store; GitHub Pages receives lightweight HTML, posters and selected
preview assets.

```bash
python -u tools/sync_stable_pixelworld_pages.py \
  --run-root runs/bench_codex_20261109 \
  --heavy-web /path/to/heavy/web \
  --stable-repo /path/to/SiyuanMaCS.github.io \
  --status runs/bench_codex_20261109/stable_pixelworld_refresh_status.json \
  --core-only \
  --delivery-repo /tmp/pixelworld-pages-delivery
```

After publication, verify the public page with a cache-busting query:

```bash
curl -L 'https://siyuanmacs.github.io/pixelworld/index.html?refresh=manual' | head
```

## 7. What to upload

Commit to GitHub:

- benchmark config templates;
- protocol docs;
- evaluator setup notes;
- small JSON provenance summaries;
- code changes and tests.

Do not commit:

- API keys or Codex auth files;
- full generated tensors/videos;
- Physics-IQ reference videos/masks;
- PCVE target videos or evaluator-only ground truth;
- per-case raw traces unless explicitly sampled and size-bounded.

For returning a finished Kimi/GLM/Qwen run to the benchmark owner, package a
manifest with:

- config JSON and git commit;
- model id/provider id;
- run root path or artifact-store URI;
- status JSON;
- evaluation result JSON;
- dashboard URL.

Generated tensors and videos belong in the agreed artifact store, not a pull
request. The owner adds the exact model key to the same dashboard registry;
model rows are not special-cased in page code.
