from __future__ import annotations

from pathlib import Path
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import threading

from .audit import canonicalize_tensor, validate
from .config import Benchmark, Model
from .constants import ASSET_ROOT, TRACKS, LEGACY_TRACK_PROTOCOLS
from .util import atomic_json, sha256, utc_now
from .usage import collect_usage, preserve_completed_request_bound
from .events import capture_stream
from .media import contract, task_rules


FILE_CHANGE_STALL_SECONDS = 20 * 60
# Queued provider requests may legitimately wait a long time before the first
# token. Do not kill them; queueing is expected and recovers on its own.
FIRST_TOKEN_STALL_SECONDS = 30 * 60
WATCHDOG_POLL_SECONDS = 10
CLI_TEARDOWN_GRACE_SECONDS = 60


def _toml(value: str) -> str:
    return json.dumps(value)


def generation_task_text(track: str, spec: dict, *, legacy: bool = False,
                         token_budget_enabled: bool = True, override: Path | None = None) -> str:
    root = ASSET_ROOT / 'legacy_v1' if legacy else ASSET_ROOT
    text = (root / {'text':'TASK_TEXT.md','vision-input':'TASK_VISION_INPUT.md',
                    'vision-agent':'TASK_VISION_AGENT.md'}[track]).read_text()
    if spec['task_type'] == 'v2v':
        text = ('Generate the future RGB continuation requested in `prompt.txt`, following `GENERATION_RULES.md`. '
                'The supplied conditioning video and frames contain only the historical segment. '
                'Use the permitted tools and choose your own approach to generation, checking and revision.\n')
    elif spec['task_type'] == 'video_edit':
        text = ('Edit the complete source video as requested in `prompt.txt`, following `GENERATION_RULES.md`. '
                'You must create and run `generate.py`, and call `artifact_io.save(...)` at least once before finishing. '
                'If you revise the video, save each revised candidate before continuing. '
                + ('Visual image tools are available.\n' if track == 'vision-agent' else
                   'The source is available through local code and numeric RGB operations; visual image tools are disabled.\n'))
    elif spec['task_type'] == 't2v':
        text = ('Generate the RGB video requested in `prompt.txt`, following `GENERATION_RULES.md` and `task_spec.json`. '
                'There is no input image. Choose your own approach. '
                + ('Visual image tools are available; choose whether to inspect your output.\n' if track == 'vision-agent'
                   else 'Visual image tools are disabled.\n'))
    if override:
        return override.read_text()
    if token_budget_enabled and not legacy:
        text += '\nAim for high-quality output while considering efficiency.\n'
    return text


def _seed_provider_env(environment: dict[str, str], config: Benchmark, model: Model) -> None:
    key = model.provider.env_key
    if not key or environment.get(key):
        return
    filenames = {
        "PIXELWORLD_API_KEY": "local.key",
        "PIXELWORLD_MUX_KEY": "mux.key",
    }
    filename = filenames.get(key)
    if not filename:
        return
    roots = [config.runs_root, config.source.parent]
    for root in roots:
        for parent in (root, *root.parents):
            candidate = parent / "queue_proxy_logs" / filename
            if candidate.is_file():
                value = candidate.read_text().strip()
                if value:
                    environment[key] = value
                    return


def codex_command(model: Model, track: str, workspace: Path,
                  codex_binary: str = "codex", has_image: bool = True,
                  filesystem_policy: str = "workspace-write") -> list[str]:
    if track not in TRACKS or track not in model.tracks:
        raise ValueError(f"Track {track} is not enabled for {model.key}")
    from .permissions import CASE_ONLY_POLICY, WORKSPACE_WRITE_POLICY
    if filesystem_policy not in (CASE_ONLY_POLICY, WORKSPACE_WRITE_POLICY):
        raise ValueError("Unknown filesystem policy")
    if filesystem_policy == CASE_ONLY_POLICY and (model.cli_backend not in ("codex", "pi") or track == "vision-input"):
        raise ValueError("case-only-v1 requires native Codex Text/Vision tracks")
    if model.cli_backend=='pi':
        native_command=[sys.executable,'-m','pixelworld.native_harness','--workspace',str(workspace),
                '--state',str(workspace.parent/'empty-codex-home'),'--model',model.model,
                '--reasoning',model.reasoning_effort,'--base-url',model.provider.base_url,
                '--env-key',model.provider.env_key,'--track',track]
        if model.provider.model_catalog_json:
            native_command+=['--model-catalog',model.provider.model_catalog_json]
        return native_command
    if model.cli_backend == "trae":
        command = [codex_binary, "exec", "-", "--model", model.model,
                   "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   "--skip-git-repo-check", "--sandbox", "workspace-write",
                   "--strict-config", "--json", "-C", str(workspace)]
        for value in ('model_provider="trae"', f'model_reasoning_effort="{model.reasoning_effort}"',
                      'approval_policy="never"', 'sandbox_workspace_write.network_access=false',
                      'sandbox_workspace_write.writable_roots=[]', 'sandbox_workspace_write.exclude_slash_tmp=true',
                      'web_search="disabled"', 'memories.use_memories=false', 'memories.generate_memories=false',
                      'skills.bundled.enabled=false', 'resource_dirs=[]', 'history.persistence="none"',
                      'shell_environment_policy.inherit="none"', 'features.multi_agent_v2.max_concurrent_threads_per_session=1'):
            command += ['-c', value]
        for feature in ('browser_use','computer_use','image_generation','multi_agent','multi_agent_v2',
                        'apps','plugins','memories','skill_search','hooks'):
            command += ['--disable',feature]
        if track in ('text','vision-input'):
            command += ['--disallowed-tool','Read','--disallowed-tool','view_image']
        elif has_image:
            command += ['-i',str(workspace/'first_frame.png')]
        command += ['--shell-tool-timeout','10m']
        return command
    python_bin = str(Path(sys.executable).resolve().parent)
    safe_path = ":".join(dict.fromkeys((python_bin, "/usr/local/bin", "/usr/bin", "/bin")))
    command = [
        codex_binary, "--ask-for-approval", "never", "exec", "-", "--model", model.model,
        "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "workspace-write",
        "--strict-config", "--json",
        "-C", str(workspace),
        "-c", f"model_reasoning_effort={_toml(model.reasoning_effort)}",
        "-c", "sandbox_workspace_write.network_access=false",
        "-c", "sandbox_workspace_write.writable_roots=[]",
        "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
        "-c", "project_doc_max_bytes=0",
        "-c", "skills.bundled.enabled=false",
        "-c", "shell_environment_policy.inherit=none",
        "-c", f"shell_environment_policy.set.PATH={_toml(safe_path)}",
        "-c", 'web_search="disabled"',
    ]
    if model.auto_compact_token_limit is not None:
        command += ["-c", f"model_auto_compact_token_limit={model.auto_compact_token_limit}"]
    if filesystem_policy == CASE_ONLY_POLICY:
        from .permissions import case_permissions, permission_args
        index = command.index("--sandbox")
        del command[index:index + 2]
        # A named profile owns the filesystem and network rules; do not mix it
        # with the deprecated workspace-write permission selection.
        command += permission_args(case_permissions(workspace))
    # Library imports can create caches without an explicit agent file write.
    # Keep scratch/cache output inside this run on the cloud filesystem.
    scratch = workspace / ".tmp"
    isolated_environment = {
        "TMPDIR": str(scratch),
        "XDG_CACHE_HOME": str(scratch / "cache"),
        "XDG_CONFIG_HOME": str(scratch / "config"),
        "MPLCONFIGDIR": str(scratch / "matplotlib"),
        "NUMBA_CACHE_DIR": str(scratch / "numba"),
        "HF_HOME": str(scratch / "hf"),
        "PYTHONPYCACHEPREFIX": str(scratch / "pycache"),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OPENCV_FOR_THREADS_NUM": "2",
    }
    for key, value in isolated_environment.items():
        command += ["-c", f"shell_environment_policy.set.{key}={_toml(value)}"]
    if model.provider.type == "responses":
        provider = model.provider
        command.extend([
            "-c", f"model_provider={_toml(provider.id or '')}",
            "-c", f"model_providers.{provider.id}.name={_toml(provider.name or '')}",
            "-c", f"model_providers.{provider.id}.base_url={_toml(provider.base_url or '')}",
            "-c", f"model_providers.{provider.id}.wire_api=\"responses\"",
            "-c", f"model_providers.{provider.id}.env_key={_toml(provider.env_key or '')}",
        ])
        if provider.model_catalog_json:
            command += ['-c',f'model_catalog_json={_toml(provider.model_catalog_json)}']
        if provider.stream_idle_timeout_ms is not None:
            command += ['-c',f'model_providers.{provider.id}.stream_idle_timeout_ms={provider.stream_idle_timeout_ms}']
        command += ['-c',f'model_providers.{provider.id}.request_max_retries=6',
                    '-c',f'model_providers.{provider.id}.stream_max_retries=6']
    elif model.provider.type == "oss":
        command.extend(["--oss"])

    # These are Codex feature gates, not prompt-only requests.
    for feature in ("browser_use", "computer_use", "image_generation", "multi_agent", "multi_agent_v2",
                    "apps", "plugins", "memories", "skill_search", "hooks"):
        command.extend(["--disable", feature])
    if track in ("text", "vision-input"):
        command.extend(["--disable", "view_image"])
    if track != "text" and has_image:
        command.extend(["--image", str(workspace / "first_frame.png")])
    return command


def _last_event(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 128 * 1024))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return None


def _provider_infra_failure(events: Path) -> str | None:
    try:
        # Provider failures terminate the turn and therefore live at the tail.
        # Some 4K agent traces are hundreds of MiB; scanning them in full stalls
        # finalization and historical reclassification on network storage.
        with events.open('rb') as stream:
            stream.seek(0,os.SEEK_END)
            stream.seek(max(0,stream.tell()-512*1024))
            text=stream.read().decode('utf-8',errors='replace')
    except OSError:
        return None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') not in ('error', 'turn.failed'):
            continue
        error = json.dumps(event, ensure_ascii=False).lower()
        if 'code 4055' in error or 'too many requests were sent in a short period' in error:
            return 'provider_rate_limited'
    if "request queue size is exceed" in text or "Code 4050" in text or '"code": 4050' in text:
        return "provider_queue_exceeded"
    if "Connection failed: error sending request" in text:
        return "provider_connection_failed"
    if "stream disconnected before completion: error sending request" in text:
        return "provider_connection_failed"
    if "Request exceeds local size limit" in text:
        return "local_proxy_request_limit"
    if "Local proxy authentication required" in text:
        return "local_proxy_auth_failure"
    if ("HTTPSConnectionPool" in text and any(value in text for value in
            ("Read timed out", "Max retries exceeded", "SSLError"))):
        return "provider_connection_failed"
    if any(value in text for value in ("model service is unavailable",
            "server overload", "unable to handle additional requests")):
        return "provider_connection_failed"
    if "No space left on device" in text or "storage_backpressure:" in text:
        return "storage_infrastructure_failure"
    return None


def _execution_infra_failure(logs: Path) -> str | None:
    """Identify native sandbox-launch errors, never an assistant's excuse."""
    stderr = logs / 'stderr.log'
    events = logs / 'events.jsonl'
    if (stderr.is_file() and (not events.is_file() or events.stat().st_size == 0)
            and '--trust cannot be combined with --ignore-user-config' in stderr.read_text(errors='replace')):
        return 'local_cli_configuration_failure'
    archive = logs / 'full_rollout.jsonl'
    if not archive.is_file():
        return None
    for line in archive.open(encoding='utf-8', errors='replace'):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get('payload') or {}
        values = payload.get('items', []) if event.get('type') == 'history_mutation' else (
            [payload] if event.get('type') == 'response_item' else [])
        for item in values:
            if item.get('type') not in ('function_call_output', 'custom_tool_call_output'):
                continue
            output = str(item.get('output', ''))
            if 'linux-sandbox/src/launcher.rs' in output and 'bubblewrap is unavailable' in output:
                return 'sandbox_dependency_missing'
    return None


def _wait(agent: subprocess.Popen, events: Path, logs: Path,
          stall_seconds: int = FILE_CHANGE_STALL_SECONDS,
          token_monitor=None,
          teardown_grace_seconds: int = CLI_TEARDOWN_GRACE_SECONDS,
          first_token_stall_seconds: int = FIRST_TOKEN_STALL_SECONDS) -> tuple[int, dict | None]:
    watchdog = None
    last_records = -1
    started = time.monotonic()
    first_token_seen = False
    while agent.poll() is None:
        if token_monitor is not None:
            exhausted = token_monitor.poll()
            if token_monitor.record_count > 0:
                first_token_seen = True
            if token_monitor.record_count != last_records:
                atomic_json(logs/'token_budget.json', token_monitor.report())
                last_records = token_monitor.record_count
            if exhausted or token_monitor.unavailable:
                watchdog = {'kind': 'token_budget', 'reason': 'output_token_budget_exhausted' if exhausted else 'token_usage_unavailable',
                            'cutoff_utc': token_monitor.crossed_utc or utc_now(),
                            'triggered_utc': utc_now(), 'signal': 'SIGTERM',
                            'token_budget': token_monitor.report()}
                atomic_json(logs/'watchdog.json', watchdog)
                from .token_budget import stop_agent
                stop_agent(agent)
                break
            if not first_token_seen and time.monotonic() - started >= first_token_stall_seconds:
                watchdog = {'kind': 'first_token_stall',
                            'reason': 'provider_first_token_stall',
                            'triggered_utc': utc_now(),
                            'idle_seconds': round(time.monotonic() - started, 1),
                            'signal': 'SIGTERM',
                            'token_budget': token_monitor.report()}
                atomic_json(logs/'watchdog.json', watchdog)
                from .token_budget import stop_agent
                stop_agent(agent)
                break
        time.sleep(min(WATCHDOG_POLL_SECONDS, .25) if token_monitor is not None else WATCHDOG_POLL_SECONDS)
        if agent.poll() is not None:
            break
        event = _last_event(events)
        # The model has explicitly finished. Bound CLI teardown only; never
        # use this timer while it is generating, checking or waiting for API.
        if event and event.get('type') == 'turn.completed':
            output = event.get('usage', {}).get('output_tokens')
            idle = time.time() - events.stat().st_mtime
            if isinstance(output, int) and not isinstance(output, bool) and output >= 0 and idle >= teardown_grace_seconds:
                terminal_source = logs/'events.raw.jsonl'
                if not terminal_source.is_file():
                    terminal_source = events
                watchdog = {'kind': 'cli_shutdown', 'reason': 'CLI did not exit after successful terminal event',
                            'triggered_utc': utc_now(), 'idle_seconds': idle,
                            'terminal_completed_utc': event.get('_harness_received', {}).get('utc'),
                            'events_sha256': sha256(events), 'usage_output_tokens': output,
                            'terminal_source': terminal_source.name, 'terminal_sha256': sha256(terminal_source)}
                from .token_budget import stop_agent
                stop_agent(agent)
                watchdog['process_exit_status'] = agent.returncode
                atomic_json(logs/'cli_shutdown.json', watchdog)
                atomic_json(logs/'watchdog.json', watchdog)
                break
        if not event or event.get("type") != "item.started":
            continue
        item = event.get("item", {})
        if item.get("type") != "file_change" or item.get("status") != "in_progress":
            continue
        idle = time.time() - events.stat().st_mtime
        if idle < stall_seconds:
            continue
        watchdog = {
            "triggered_utc": utc_now(),
            "reason": "file_change remained in_progress with no new JSONL event",
            "idle_seconds": idle,
            "item_id": item.get("id"),
            "signal": "SIGTERM",
        }
        atomic_json(logs / "watchdog.json", watchdog)
        agent.terminate()
        try:
            agent.wait(timeout=30)
        except subprocess.TimeoutExpired:
            watchdog["signal"] = "SIGKILL"
            atomic_json(logs / "watchdog.json", watchdog)
            agent.kill()
            agent.wait(timeout=10)
        break
    return agent.returncode, watchdog


def run_path(config: Benchmark, model: Model, track: str, case_id: str, attempt: int) -> Path:
    return config.runs_root / model.key / track / f"case_{case_id}_attempt_{attempt:02d}"


def _prior_attempt_usage(config: Benchmark, model: Model, track: str, case_id: str,
                         attempt: int) -> tuple[int, bool, list[dict]]:
    from .retries import budget_window
    window = budget_window(config, model, track, case_id, attempt)
    floor = window["start_attempt"] if window else 1
    prior_output, prior_unknown = 0, False
    ignored = []
    for index in range(floor, attempt):
        try:
            previous = json.loads((run_path(config, model, track, case_id, index)/'run_manifest.json').read_text())
            usage = previous.get('token_usage') or {}
            amount = usage.get('output_tokens')
        except (OSError, ValueError):
            usage, amount, previous = {}, None, {}
        if isinstance(amount, int) and not isinstance(amount, bool):
            prior_output += amount
            continue
        lower_bound = usage.get('reported_completed_request_output_tokens')
        budget_terminal = previous.get('stop_reason') in (
            'output_token_budget_exhausted', 'token_usage_unavailable')
        if isinstance(lower_bound, int) and not isinstance(lower_bound, bool) and not budget_terminal:
            prior_output += lower_bound
            ignored.append({
                'attempt': index,
                'previous_status': previous.get('status'),
                'usage_status': usage.get('status'),
                'counted_lower_bound_output_tokens': lower_bound,
                'reason': 'terminal usage missing but completed request lower bound is available',
            })
            continue
        # A provider failure or externally killed worker without accountable
        # completed request output should not consume a full retry budget.
        if not budget_terminal and usage.get('usage_events') == 0:
            ignored.append({
                'attempt': index,
                'previous_status': previous.get('status'),
                'usage_status': usage.get('status'),
                'reason': 'no completed request usage before retry',
            })
            continue
        prior_unknown = True
        break
    return prior_output, prior_unknown, ignored


def run_case(config: Benchmark, model_key: str, track: str, case_id: str, attempt: int = 1,
             codex_binary: str | None = None, dry_run: bool = False,
             task_file: Path | None = None, rules_file: Path | None = None,
             extra_inputs: tuple[str,...] = ()) -> dict:
    model = config.model(model_key)
    from .retries import budget_window
    retry_window = budget_window(config, model, track, case_id, attempt)
    token_limit = config.output_token_budget
    if token_limit is not None:
        prior_output, prior_unknown, ignored_prior_usage = _prior_attempt_usage(
            config, model, track, case_id, attempt)
    else:
        prior_output, prior_unknown, ignored_prior_usage = 0, False, []
    source = config.inputs_root / case_id
    spec = contract(source)
    has_image = spec["task_type"] in ("i2v", "v2v", "video_edit")
    if (has_image and not (source / "first_frame.png").is_file()) or not (source / "prompt.txt").is_file():
        raise FileNotFoundError(f"Missing prepared input for case {case_id}: {source}")
    run = run_path(config, model, track, case_id, attempt)
    if run.exists():
        raise FileExistsError(f"Run already exists: {run}")
    workspace, logs, trace = run / "workspace", run / "logs", run / "trace"
    codex_home = run / "empty-codex-home"
    for path in (workspace / ".tmp", logs, trace, codex_home):
        path.mkdir(parents=True, exist_ok=True)
    legacy_contract = config.protocol in LEGACY_TRACK_PROTOCOLS or track == "vision-input"
    task_root = ASSET_ROOT / "legacy_v1" if legacy_contract else ASSET_ROOT
    for origin, target in (
        (source / "first_frame.png", workspace / "first_frame.png"),
        (source / "prompt.txt", workspace / "prompt.txt"),
        (task_root / "GENERATION_RULES.md", workspace / "GENERATION_RULES.md"),
        (ASSET_ROOT / ("artifact_io_large.py" if spec['width']*spec['height']*spec['frames']*3>100*1024*1024 and not spec.get('audio') else "artifact_io.py"), workspace / "artifact_io.py"),
        (ASSET_ROOT / "player_template.html", workspace / "player_template.html"),
    ):
        if origin.is_file():
            shutil.copy2(origin, target)
    if spec['task_type']=='v2v':
        for name in ('conditioning.mp4','conditioning_info.json','conditioning_inspection.png'):
            if not (source/name).is_file():raise FileNotFoundError(f'Missing historical input: {name}')
            shutil.copy2(source/name,workspace/name)
        shutil.copytree(source/'conditioning_frames',workspace/'conditioning_frames')
    if spec['task_type']=='video_edit':
        for name in ('source_video.mp4','source_pixels.npz','source_info.json','source_inspection.png'):
            if not (source/name).is_file():
                raise FileNotFoundError(f'Missing declared editing input: {name}')
            shutil.copy2(source/name,workspace/name)
    for name in extra_inputs:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Extra inputs must be relative paths within the prepared case')
        origin=(source/name).resolve()
        origin.relative_to(source.resolve())
        target=workspace/name
        if origin.is_dir():shutil.copytree(origin,target)
        else:shutil.copy2(origin,target)
    task = task_root / {
        "text": "TASK_TEXT.md",
        "vision-input": "TASK_VISION_INPUT.md",
        "vision-agent": "TASK_VISION_AGENT.md",
    }[track]
    atomic_json(run / "media_contract.json", spec)
    if (source / "task_spec.json").is_file():
        if legacy_contract:
            raise ValueError("Legacy contracts cannot override media")
        atomic_json(workspace / "task_spec.json", spec)
        (workspace / "GENERATION_RULES.md").write_text(task_rules(spec))
    if rules_file:
        shutil.copy2(rules_file,workspace/'GENERATION_RULES.md')
    flash_record = None
    if config.harness_variant in ('flash-v1', 'flash-lean'):
        from .flash import install as install_flash
        flash_record = install_flash(workspace, spec, source,
                                     include_summary=config.harness_variant == 'flash-v1')
    elif config.harness_variant in ('flash-api', 'flash-api-receipt'):
        from .flash import install_api
        flash_record = install_api(workspace,
                                   receipt=config.harness_variant == 'flash-api-receipt')
    # Keep the exact supplied task text outside the model's workspace for audit.
    task_snapshot = run / "task_prompt.txt"
    task_text = generation_task_text(track, spec, legacy=legacy_contract,
                                    token_budget_enabled=token_limit is not None, override=task_file)
    if config.harness_variant=='flash-taskpack':
        from .flash import pack_task
        task_text,flash_record=pack_task(task_text,workspace)
    if config.harness_variant in ('flash-submit','flash-submit-boundary'):
        task_text += ('\nWhen satisfied with the saved video, call submit_video to check and submit it. '
                      'Fix reported errors; finish after accepted=true. This tool checks the technical '
                      'delivery contract, not appearance or physics.\n')
    task_snapshot.write_text(task_text)
    # Preserve only the supplied workspace inputs, before the model can edit them.
    # This directory stays outside the agent's visible workspace.
    input_snapshot = run / 'input_snapshot'
    input_snapshot.mkdir()
    for path in workspace.iterdir():
        if path.name == '.tmp':
            continue
        if path.is_dir():
            shutil.copytree(path, input_snapshot/path.name)
        elif path.is_file():
            shutil.copy2(path, input_snapshot/path.name)
    input_snapshot_hashes = {str(p.relative_to(input_snapshot)):sha256(p)
                             for p in input_snapshot.rglob('*') if p.is_file()}
    binary = codex_binary or (os.environ.get("PIXELWORLD_TRAECLI", "traecli") if model.cli_backend == 'trae'
                              else os.environ.get("PIXELWORLD_CODEX", "codex"))
    model_catalog_record = None
    if model.cli_backend=='codex' and model.provider.model_catalog_json:
        from dataclasses import replace
        catalog_source=Path(model.provider.model_catalog_json)
        catalog_snapshot=run/'model_catalog.json'
        shutil.copy2(catalog_source,catalog_snapshot)
        model_catalog_record={'file':'model_catalog.json','sha256':sha256(catalog_snapshot)}
        model=replace(model,provider=replace(model.provider,model_catalog_json=str(catalog_snapshot)))
    command = codex_command(model, track, workspace, binary, has_image,
                            filesystem_policy=config.filesystem_policy)
    if config.harness_variant in ('flash-submit','flash-submit-boundary'):
        module_root = Path(__file__).resolve().parents[1]
        # Codex0.169.0 forbids --trust together with --ignore-user-config.
        # The provider still reads only this fresh case-specific empty home.
        command.remove('--ignore-user-config')
        (codex_home / 'config.toml').write_text('')
        # Trust only this operator-vetted MCP command in the isolated Codex
        # home. The existing named case-only filesystem policy is unchanged.
        command += ['--trust', '-c', 'mcp_servers.pixelworld_submit.command=' + _toml(sys.executable),
                    '-c', 'mcp_servers.pixelworld_submit.args=' + json.dumps(
                        ['-m', 'pixelworld.submission', '--run', str(run)]),
                    '-c', 'mcp_servers.pixelworld_submit.env.PYTHONPATH=' + _toml(str(module_root)),
                    '-c', 'mcp_servers.pixelworld_submit.tool_timeout_sec=180']
        flash_record = {'schema': 'pixelworld-submit-tool-v1',
                        'module_sha256': sha256(module_root / 'pixelworld/submission.py'),
                        'physics_score_provided': False, 'generation_tools_unchanged': True,
                        'codex_config_scope': 'empty case-specific CODEX_HOME with vetted MCP trust',
                        'automatic_harness_stop_enabled': config.harness_variant=='flash-submit-boundary'}
    permissions_record = None
    if config.filesystem_policy == 'case-only-v1':
        from .permissions import case_permissions
        permissions_file = run / 'permissions_profile.json'
        atomic_json(permissions_file, case_permissions(workspace))
        permissions_record = {'file': permissions_file.name, 'sha256': sha256(permissions_file)}
    # Persist an external full rollout for new runs. Fresh contexts, memory and
    # history-reuse settings stay unchanged; the stdout projection omits some
    # nested tools and image returns. Preserve legacy launch settings.
    full_trace_enabled = not legacy_contract or token_limit is not None
    if full_trace_enabled and model.cli_backend!='pi':
        command.remove('--ephemeral')
    if spec['task_type']=='v2v' and track!='text' and model.cli_backend!='pi':
        command += ['-i' if model.cli_backend=='trae' else '--image',str(workspace/'conditioning_inspection.png')]
    if spec['task_type']=='video_edit' and track!='text' and model.cli_backend!='pi':
        command += ['-i' if model.cli_backend=='trae' else '--image',str(workspace/'source_inspection.png')]
    try:
        if model.cli_backend=='pi':
            from .native_harness import version as native_version
            version=native_version()
        else:
            version = subprocess.check_output([binary, "--version"], text=True).strip()
    except Exception:
        version = "unavailable"
    manifest = {
        "status": "dry-run" if dry_run else "running",
        "started_utc": utc_now(),
        "protocol": config.protocol,
        "generation_contract": "legacy-v1" if legacy_contract else "minimal-v2",
        "model_key": model.key,
        "model": model.model,
        "cli_backend": model.cli_backend,
        "agent_harness_version":version,
        "provider": model.provider.__dict__,
        "model_catalog_snapshot": model_catalog_record,
        "reasoning_effort": model.reasoning_effort,
        "auto_compact_token_limit": model.auto_compact_token_limit,
        "track": track,
        "initial_image_attached": has_image and track != "text",
        "output_image_tool_enabled": track == "vision-agent",
        "case_id": case_id,
        "attempt": attempt,
        "workspace": str(workspace),
        "codex_version": version,
        "runtime_helpers": ({"bwrap_sha256": sha256(Path(binary).resolve().parent / 'bwrap')}
                            if (Path(binary).resolve().parent / 'bwrap').is_file() else {}),
        "isolated_codex_home": str(codex_home),
        "filesystem_policy": config.filesystem_policy,
        "harness_variant": config.harness_variant,
        "flash_tools": flash_record,
        "permissions_profile": permissions_record,
        "command": command,
        "task_sha256": sha256(task_snapshot),
        "task_prompt_file": "task_prompt.txt",
        "rules_sha256": sha256(workspace / "GENERATION_RULES.md"),
        "input_image_sha256": sha256(workspace / "first_frame.png") if has_image else None,
        "media_contract": spec,
        "input_prompt_sha256": sha256(workspace / "prompt.txt"),
        "initial_visible_files": sorted(path.name for path in workspace.iterdir() if path.is_file()),
        "initial_input_snapshot": 'input_snapshot/',
        "initial_input_hashes": input_snapshot_hashes,
        "extra_input_files": list(extra_inputs),
        "full_trace_capture_enabled": full_trace_enabled,
        "output_token_budget": token_limit,
        "prior_attempt_output_tokens": prior_output,
        "prior_attempt_usage_ignored": ignored_prior_usage,
        "retry_authorization": retry_window,
        "budget_scope_start_attempt": retry_window["start_attempt"] if retry_window else 1,
        "token_usage": collect_usage(logs / "events.jsonl", finished=False),
    }
    atomic_json(run / "run_manifest.json", manifest)
    if dry_run:
        return manifest
    if token_limit is not None and (prior_unknown or prior_output >= token_limit):
        manifest.update({'status': 'failed', 'retryable': False,
                         'stop_reason': 'prior_token_usage_unavailable' if prior_unknown else 'output_token_budget_exhausted',
                         'completed_utc': utc_now(),
                         'validation': {'pass': False, 'reason': 'No verifiable task token budget remains; model not launched'}})
        atomic_json(run/'run_manifest.json', manifest)
        from .recording import write_bundle
        manifest['recording_bundle'] = write_bundle(run)
        atomic_json(run/'run_manifest.json', manifest)
        return manifest

    started = dt.datetime.now(dt.timezone.utc)
    token_monitor = None
    stop_file = logs / "stop_watcher"
    with (logs / "watcher.log").open("w") as watcher_out, (logs / "watcher.stderr.log").open("w") as watcher_err:
        watcher = subprocess.Popen([
            sys.executable, "-m", "pixelworld.snapshot", "--workspace", str(workspace),
            "--trace", str(trace), "--stop-file", str(stop_file),
            "--spec-file", str(run / "media_contract.json"),
        ], stdout=watcher_out, stderr=watcher_err)
        environment = os.environ.copy()
        environment["TMPDIR"] = str(workspace / ".tmp")
        environment["PIXELWORLD_CLEANROOM"] = "1"
        environment["CODEX_HOME"] = str(codex_home)
        _seed_provider_env(environment, config, model)
        if model.cli_backend == 'trae':
            environment.pop('CODEX_HOME',None)
            environment['TRAE_HOME'] = str(codex_home)
            environment['TRAECLI_HOME'] = os.environ.get('TRAECLI_HOME', str(Path.home()/'.trae/cli'))
        if token_limit is not None:
            from .token_budget import UsageMonitor
            cli_home = Path(environment['TRAECLI_HOME']) if model.cli_backend == 'trae' else codex_home
            token_monitor = UsageMonitor(logs/'events.jsonl', cli_home, started, token_limit, prior_output,
                                         backend=model.cli_backend)
        with task_snapshot.open("rb") as stdin, (logs / "stderr.log").open("wb") as stderr:
            agent = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, env=environment,
                                     start_new_session=token_limit is not None)
            logger = threading.Thread(target=capture_stream, args=(agent.stdout, logs / "events.raw.jsonl", logs / "events.jsonl"), daemon=True)
            logger.start()
            (logs / "agent.pid").write_text(str(agent.pid) + "\n")
            (logs / "supervisor.pid").write_text(str(os.getpid()) + "\n")
            exit_status, watchdog = _wait(agent, logs / "events.jsonl", logs, token_monitor=token_monitor)
            logger.join(timeout=10)
        stop_file.touch()
        try:
            watcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            watcher.terminate()
            watcher.wait(timeout=10)

    full_trace = {'status': 'not_recorded', 'reason': 'Legacy ephemeral launch settings preserved'}
    if full_trace_enabled:
        from .full_trace import capture_rollout
        cli_home = Path(environment['TRAECLI_HOME']) if model.cli_backend == 'trae' else codex_home
        try:
            full_trace = capture_rollout(cli_home, logs, started)
        except (OSError, ValueError, KeyError) as error:
            full_trace = {'status':'error','reason':str(error)}
        atomic_json(logs / 'full_trace_status.json', full_trace)
    if token_monitor is not None:
        # Capture a final usage record even if the CLI exited between polls.
        crossed = token_monitor.poll()
        atomic_json(logs/'token_budget.json', token_monitor.report())
        if crossed and watchdog is None:
            watchdog = {'kind': 'token_budget', 'reason': 'output_token_budget_exhausted',
                        'cutoff_utc': token_monitor.crossed_utc, 'signal': None,
                        'observed_after_cli_exit': True, 'token_budget': token_monitor.report()}
            atomic_json(logs/'watchdog.json', watchdog)
    budget_stopped = bool(watchdog and watchdog.get('kind') == 'token_budget')
    submission_selection = None
    if budget_stopped and watchdog['reason'] == 'output_token_budget_exhausted':
        from .token_budget import restore_candidate
        submission_selection = restore_candidate(workspace, trace, logs, watchdog['cutoff_utc'],
                                                 exit_status, track, legacy_contract, spec)
    # A fast CLI exit can beat the asynchronous snapshot watcher. Preserve its
    # final stable saved output without altering any generated pixels or code.
    if not any(trace.glob("round_*/snapshot.json")):
        from .snapshot import candidate_key, freeze
        final_key = candidate_key(workspace, spec)
        if final_key is not None:
            freeze(workspace, trace, 0, final_key, spec)
    canonicalization = canonicalize_tensor(workspace, logs, spec)
    boundary_verified = False
    if config.harness_variant == 'flash-submit-boundary':
        from .submission_boundary import verify_boundary
        boundary_verified = verify_boundary(run)
    validation = validate(workspace, logs / "events.jsonl", exit_status, track,
                          require_reports=legacy_contract, media=spec,
                          budget_frozen=submission_selection is not None,
                          submission_requested=boundary_verified)
    if boundary_verified:
        validation['completion_mode']='verified_submission_boundary'
    validation.update({
        "track": track,
        "initial_image_attached": has_image and track != "text",
        "output_image_tool_enabled": track == "vision-agent",
    })
    atomic_json(logs / "validation.json", validation)
    completed = dt.datetime.now(dt.timezone.utc)
    token_usage = preserve_completed_request_bound(collect_usage(logs / "events.jsonl"),
                          token_monitor.report() if token_monitor is not None else None)
    if boundary_verified:
        from .submission_boundary import completed_usage
        token_usage=completed_usage(run,token_usage)
    terminal_output = token_usage.get('output_tokens')
    if token_limit is not None and isinstance(terminal_output, int) and terminal_output + prior_output > token_limit and not budget_stopped:
        # Unsupported/late usage reporting cannot silently accept an over-budget output.
        budget_stopped = True
        watchdog = {'kind': 'token_budget', 'reason': 'output_token_budget_exhausted',
                    'signal': None, 'observed_after_cli_exit': True,
                    'accounting': 'terminal_only', 'reported_output_tokens': terminal_output + prior_output}
        validation['pass'] = False
        validation['budget_error'] = 'Over budget without a verified request-level candidate boundary'
        atomic_json(logs/'validation.json', validation)
        atomic_json(logs/'watchdog.json', watchdog)
    if budget_stopped and token_usage['status'] != 'recorded' and token_monitor is not None:
        token_usage.update({'status': 'partial' if token_monitor.record_count else 'missing',
                            'reported_completed_request_output_tokens': token_monitor.output if token_monitor.record_count else None,
                            'inflight_usage_unknown': True,
                            'source_note': 'Completed request tokens are a lower bound after interruption, not a final run total.'})
    provider_infra_failure = (
        None if budget_stopped or validation["pass"] else _provider_infra_failure(logs / "events.jsonl")
    )
    if not provider_infra_failure and not budget_stopped and not validation['pass']:
        provider_infra_failure = _execution_infra_failure(logs)
    if (not provider_infra_failure and not budget_stopped and not validation["pass"]
            and watchdog and watchdog.get("reason") == "provider_first_token_stall"):
        provider_infra_failure = "provider_first_token_stall"
    if provider_infra_failure:
        validation["infra_error"] = provider_infra_failure
    atomic_json(logs / "token_usage.json", token_usage)
    manifest.update({
        "status": "complete" if validation["pass"] else "failed",
        "completed_utc": completed.isoformat(),
        "duration_seconds": (completed - started).total_seconds(),
        "exit_status": exit_status,
        "trace_rounds": len(list(trace.glob("round_*"))),
        "validation": validation,
        "token_usage": token_usage,
        "full_trace": full_trace,
        "recording_coverage": {
            "stdout_stream_finished": not logger.is_alive(),
            "full_rollout_status": full_trace.get('status'),
            "unresolved_blobs": full_trace.get('unresolved_blobs'),
            "interrupted_by_budget": budget_stopped,
            "usage_status": token_usage['status'],
            "scope": 'CLI-exposed messages and artifacts; not unreturned provider internals or unfinished responses',
            "candidate_history": 'Stable saved snapshots observed externally; rapid overwrites and unsaved drafts may be absent',
        },
        "token_budget": token_monitor.report() if token_monitor is not None else None,
        "submission_selection": submission_selection,
        "stop_reason": (watchdog['reason'] if budget_stopped else
                        'verified_submission_boundary' if boundary_verified else provider_infra_failure),
        "retryable": not budget_stopped,
    })
    if watchdog:
        manifest["watchdog"] = watchdog
    if canonicalization:
        manifest["tensor_canonicalization"] = canonicalization
    for name, key in (("generate.py", "generate_sha256"), ("pixels.npz", "pixels_sha256")):
        if (workspace / name).is_file():
            manifest[key] = sha256(workspace / name)
    atomic_json(run / "run_manifest.json", manifest)
    try:
        from .recording import write_bundle
        manifest['recording_bundle'] = write_bundle(run)
    except (OSError, ValueError, KeyError) as error:
        manifest['recording_bundle'] = {'schema': 'pixelworld-recording-v1', 'status': 'error',
                                        'reason': str(error), 'training_ready': False}
    atomic_json(run / 'run_manifest.json', manifest)
    return manifest
