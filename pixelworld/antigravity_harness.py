"""Pinned native Antigravity ACP with direct-RGB tools and external OAuth.

The fixed-upstream host relay records raw model I/O and native token usage.
Agent file and shell tools run in a per-case filesystem with a non-secret
credential placeholder; only the external relay can read the operator login.
The native catalog command refreshes the one canonical login under a lock.
ACP supplies first-frame and source-inspection images without adding a renderer.
"""
from __future__ import annotations

from contextlib import ExitStack
import base64
import json
import os
import fcntl
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

from . import antigravity_sandbox as ags
from . import claude_process_sandbox as cps
from .cloudcode_relay import cloudcode_relay, output_tokens
from .egress_allowlist_proxy import ANTIGRAVITY_HOSTS, egress_proxy
from .token_budget import ResponseUsageBudget, stop_agent
from .util import atomic_bytes, atomic_json, sha256, utc_now, write_stream_bytes

PROVIDER = 'google_subscription'
BACKEND = 'antigravity'
VERSION = '1.5.18'
AGENT_NAME = 'pixelworld'
# Model-visible tools of the custom agent (manage_task is added by agy itself
# for background commands and cannot be removed).
AGENT_TOOLS = ('read_file', 'run_command', 'write_file', 'edit_file')
ALLOWED_TOOLS = frozenset((*AGENT_TOOLS, 'manage_task'))
# Request types the relay forwards for the pinned model.
AGENT_REQUEST_TYPES = ('custom_agent', 'agent')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff')

# The custom agent's body is agy's own default <identity> section, verbatim.
AGENT_MARKDOWN = '''---
name: pixelworld
description: PixelWorld case agent with shell and file tools only.
tools:
  - read_file
  - run_command
  - write_file
  - edit_file
subagent: false
mainAgent: true
---

# System Prompt
You are Antigravity, a powerful agentic AI assistant designed by the Google Deepmind team.
You are working with a USER to solve their task.
The USER will send you requests, which you must always prioritize addressing. User requests are enclosed within <USER_REQUEST> tags.
'''


# Agent file tools (read_file/write_file/edit_file run inside the agy process,
# not in the terminal sandbox) may not touch the private HOME, which holds the
# credential copy and agy's own state, nor the pinned client/runtime/bridges.
DENIED_TOOL_PATHS = (ags.INNER_HOME, ags.INNER_CLIENT, ags.INNER_RUNTIME, ags.INNER_BRIDGES)


def agy_settings():
    """Per-attempt settings.json (never the operator's own settings)."""
    deny = ['read_url(*)', 'execute_url(*)', 'download_url(*)', 'mcp(*)', 'unsandboxed(*)']
    deny += [f'{action}({target})' for path in DENIED_TOOL_PATHS for target in (path, path + '/**')
             for action in ('read_file', 'write_file')]
    return {'enableTerminalSandbox': False, 'allowNonWorkspaceAccess': False,
            'disableSlashCommands': True, 'artifactReviewPolicy': 'always-proceed',
            'useG1Credits': False, 'showFeedbackSurvey': False, 'notifications': False,
            'permissions': {'allow': [], 'ask': [], 'deny': deny}}


def client_arguments():
    # --dangerously-skip-permissions: same as the Claude route's bypassPermissions.
    # Only with it does agy's terminal sandbox mount the ACP workspace writable
    # (in default/accept-edits/auto ACP modes /workspace is read-only for commands).
    # File-tool deny rules are not reliable in this mode. No real credentials
    # are placed in the client's filesystem; the host relay owns authentication.
    return ['acp', '--disable-telemetry', '--include-default-customizations=false',
            '--dangerously-skip-permissions', '--agent', AGENT_NAME,
            '--cloud_code_endpoint', f'http://127.0.0.1:{ags.RELAY_PORT}']


def login_state_root(environment=None):
    value = (environment or os.environ).get('PIXELWORLD_AGY_STATE')
    return Path(value) if value else Path.home()/'.gemini'/'antigravity-cli'


def prepare_home(home: Path, source: Path) -> dict:
    """Populate a private HOME with a non-secret login placeholder."""
    app = home/'.gemini'/'antigravity-cli'
    for rel in ('cache/onboarding.json',):
        origin = source/rel
        if not origin.is_file():
            raise FileNotFoundError(f'agy login state is missing {rel}; log in with the host agy first')
        target = app/rel
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(origin, target)
        target.chmod(0o600)
    atomic_json(app/'antigravity-oauth-token', {'token': {
        'access_token': 'pixelworld-placeholder-not-a-credential',
        'refresh_token': 'pixelworld-no-refresh-credential', 'token_type': 'Bearer',
        'expiry': '2099-01-01T00:00:00Z', 'expires_in': 99999999},
        'auth_method': 'consumer', 'id_token_email': 'agent@example.invalid'})
    (app/'antigravity-oauth-token').chmod(0o600)
    atomic_json(app/'settings.json', agy_settings())
    agents = home/'.gemini'/'config'/'agents'
    agents.mkdir(parents=True, exist_ok=True)
    (agents/f'{AGENT_NAME}.md').write_text(AGENT_MARKDOWN)
    # Harness configuration is bind-mounted read-only over the writable HOME.
    read_only = [app/'settings.json', agents]
    return {'copied': ['cache/onboarding.json'], 'credential_placeholder': True, 'settings': agy_settings(),
            'agent_sha256': sha256(agents/f'{AGENT_NAME}.md'), 'read_only': read_only}


def operator_credentials(source: Path) -> list[str]:
    data = json.loads((source/'antigravity-oauth-token').read_text())['token']
    return [data[k] for k in ('access_token', 'refresh_token') if isinstance(data.get(k), str)]


def operator_access_token(source: Path, client: Path) -> str:
    """Serialize native refresh outside every agent mount and read its result."""
    from datetime import datetime
    def valid():
        data = json.loads((source/'antigravity-oauth-token').read_text())['token']
        expiry = datetime.fromisoformat(data['expiry'].replace('Z', '+00:00')).timestamp()
        return data['access_token'] if expiry - time.time() > 120 else None
    with (source/'pixelworld-refresh.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        token = valid()
        if token is not None:
            return token
        # The native OAuth library may consider a credential with less than
        # two minutes left still valid. Force only its local expiry metadata
        # stale so the native catalog request refreshes before our safety margin.
        path = source/'antigravity-oauth-token'
        original = path.read_bytes()
        backup = source/'pixelworld-pre-refresh.json'
        backup.write_bytes(original)
        backup.chmod(0o600)
        stale = json.loads(original)
        stale['token']['expiry'] = '2000-01-01T00:00:00Z'
        atomic_json(path, stale)
        path.chmod(0o600)
        # Only a native catalog command is run, with no agent prompt or tools.
        # All callers share this one canonical state, avoiding rotated-copy races.
        env = os.environ.copy()
        env.update(HOME=str(source.parent.parent), AGY_CLI_DISABLE_AUTO_UPDATE='1')
        env.pop('DBUS_SESSION_BUS_ADDRESS', None)
        completed = subprocess.run([str(client), 'models'], cwd=source.parent.parent,
                                   env=env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=120)
        token = valid()
        if completed.returncode != 0 or token is None:
            raise RuntimeError('Native operator login refresh did not produce a valid credential')
        backup.unlink(missing_ok=True)
        return token


def prompt_blocks(workspace: Path, task: str) -> list[dict]:
    blocks = [{'type': 'text', 'text': task}]
    for name in ('first_frame.png', 'conditioning_inspection.png', 'source_inspection.png'):
        image = workspace/name
        if image.is_file():
            blocks.append({'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(image.read_bytes()).decode()})
    return blocks


def permission_decision(request: dict) -> tuple[str, str]:
    """(optionId kind prefix, reason) for an ACP session/request_permission."""
    call = request.get('toolCall') or {}
    name = call.get('name')
    if name not in ALLOWED_TOOLS:
        return 'reject', f'tool {name!r} is outside the case allowlist'
    return 'allow', 'case allowlist'


def _option(options, prefix):
    for option in options or []:
        if str(option.get('kind', '')).startswith(prefix):
            return option.get('optionId')
    return None


class AcpDriver:
    """Minimal ACP client: one session, one prompt, permission policy, budget stop."""

    def __init__(self, command, out: Path, budget: ResponseUsageBudget, model: str, effort: str,
                 cwd: Path, env: dict, handshake_timeout=180, secrets=None):
        self.command, self.out, self.budget, self.model, self.effort = command, out, budget, model, effort
        self.cwd, self.env, self.handshake_timeout = cwd, env, handshake_timeout
        self.secrets = secrets or (lambda: [])
        self.queue: queue.Queue = queue.Queue()
        self.next_id = 0
        self.permissions = []
        self.session = None
        self.proc = None

    def _reader(self, stream, sink):
        for line in iter(stream.readline, b''):
            # The raw ACP log lands in the run directory: never let a credential string reach it.
            for value in self.secrets():
                line = line.replace(value.encode(), b'<redacted-credential>')
            write_stream_bytes(sink, line)
            sink.flush()
            try:
                self.queue.put(json.loads(line))
            except ValueError:
                pass
        self.queue.put(None)

    def _send(self, message):
        self.proc.stdin.write((json.dumps(message) + '\n').encode())
        self.proc.stdin.flush()

    def _request(self, method, params):
        self.next_id += 1
        self._send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params})
        return self.next_id

    def _handle_incoming(self, message):
        if message.get('method') == 'session/request_permission':
            kind, reason = permission_decision(message.get('params') or {})
            option = _option((message.get('params') or {}).get('options'), kind)
            call = (message.get('params') or {}).get('toolCall') or {}
            self.permissions.append({'utc': utc_now(), 'tool': call.get('name'), 'tool_call_id': call.get('toolCallId'),
                                     'decision': kind, 'reason': reason})
            outcome = {'outcome': 'selected', 'optionId': option} if option else {'outcome': 'cancelled'}
            self._send({'jsonrpc': '2.0', 'id': message['id'], 'result': {'outcome': outcome}})
        elif 'id' in message and 'method' in message:
            # No fs/terminal client capabilities are offered.
            self._send({'jsonrpc': '2.0', 'id': message['id'],
                        'error': {'code': -32601, 'message': 'not supported by the PixelWorld ACP client'}})

    def _await(self, ident, deadline=None, stop_on_budget=False):
        while True:
            if stop_on_budget and not self.budget.can_dispatch:
                return None, 'token_usage_unavailable' if self.budget.unavailable else 'output_token_budget_exhausted'
            if deadline is not None and time.monotonic() > deadline:
                return None, 'acp_handshake_timeout'
            try:
                message = self.queue.get(timeout=.25)
            except queue.Empty:
                if self.proc.poll() is not None and self.queue.empty():
                    return None, 'client_exited'
                continue
            if message is None:
                return None, 'client_exited'
            if message.get('id') == ident and 'method' not in message:
                return message, None
            self._handle_incoming(message)

    def run(self, blocks, pid_file=None):
        """Return (exit status, stop reason, prompt result, session info)."""
        info = {}
        with (self.out/'events.native.jsonl').open('wb', buffering=0) as sink, (self.out/'stderr.log').open('wb', buffering=0) as stderr:
            if not self.budget.can_dispatch:
                return 125, 'budget_already_exhausted', None, info
            self.proc = subprocess.Popen(self.command, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            if pid_file is not None:
                pid_file.write_text(str(self.proc.pid) + '\n')
            threads = [threading.Thread(target=self._reader, args=(self.proc.stdout, sink), daemon=True),
                       threading.Thread(target=_copy, args=(self.proc.stderr, stderr), daemon=True)]
            for thread in threads:
                thread.start()
            stop, result = None, None
            try:
                deadline = time.monotonic() + self.handshake_timeout
                reply, stop = self._await(self._request('initialize', {
                    'protocolVersion': 1, 'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False},
                                                                 'terminal': False}}), deadline)
                if stop is None:
                    info['agent_info'] = (reply.get('result') or {}).get('agentInfo')
                    reply, stop = self._await(self._request('session/new', {'cwd': ags.INNER_WORKSPACE,
                                                                            'mcpServers': []}), deadline)
                if stop is None:
                    session = reply.get('result') or {}
                    self.session = session.get('sessionId')
                    info['session_id'] = self.session
                    info['session_models'] = session.get('models')
                    info['session_config'] = session.get('configOptions')
                    current = (session.get('models') or {}).get('currentModelId')
                    if self.session is None:
                        stop = 'acp_session_failed'
                    elif current != self.model:
                        reply, stop = self._await(self._request('session/set_model', {
                            'sessionId': self.session, 'modelId': self.model}), deadline)
                        info['set_model'] = reply
                        if stop is None and 'error' in reply:
                            stop = 'pinned_model_unavailable'
                if stop is None:
                    info['prompt_started_utc'] = utc_now()
                    reply, stop = self._await(self._request('session/prompt', {'sessionId': self.session,
                                                                               'prompt': blocks}), stop_on_budget=True)
                    if reply is not None:
                        result = reply.get('result')
                        info['prompt_error'] = reply.get('error')
                    elif stop in ('output_token_budget_exhausted', 'token_usage_unavailable'):
                        try:
                            self._send({'jsonrpc': '2.0', 'method': 'session/cancel', 'params': {'sessionId': self.session}})
                        except OSError:
                            pass
                        time.sleep(2)
            finally:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass
                try:
                    self.proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
                if self.proc.poll() is None:
                    stop_agent(self.proc)
                for thread in threads:
                    thread.join(timeout=30)
            if stop == 'client_exited' and result is None:
                stop = None if self.proc.returncode == 0 else 'client_exited'
            return self.proc.returncode, stop, result, info


def _copy(source, sink):
    for chunk in iter(lambda: source.read1(65536), b''):
        write_stream_bytes(sink, chunk)
        sink.flush()


def host_view(value, workspace):
    if not isinstance(value, str):
        return value
    aliases = {ags.INNER_WORKSPACE: str(workspace)}
    if os.environ.get('PIXELWORLD_PRIVATE_TMP') == '1':
        # Audit namespace aliases; raw ACP/wire bytes retain native paths.
        aliases['/tmp'] = str(workspace/'.agy-private-tmp')
        aliases[ags.INNER_HOME+'/.gemini/antigravity-cli/brain'] = str(workspace/'.agy-private-brain')
    runtime = os.environ.get('PIXELWORLD_CONTROLLED_RUNTIME')
    if runtime:
        for part in ('bin', 'lib'):
            aliases[ags.INNER_RUNTIME+'/'+part] = str(Path(runtime)/part)
    roots = '|'.join(re.escape(source) for source in sorted(aliases, key=len, reverse=True))
    pattern = r'(?<![\w./-])(?P<root>'+roots+r''')(?=/|[\s'";,:)]|$)'''
    return re.sub(pattern, lambda match: aliases[match['root']], value)


def _exit_code(raw, text):
    code = raw.get('exit_code') if isinstance(raw, dict) else None
    if type(code) is int:
        return code
    found = re.search(r'Command exited with code (-?\d+)', text or '')
    if found:
        return int(found[1])
    if 'Command failed to run:' in (text or ''):
        return 1
    return None


def projected_item(name, args, raw, status, workspace):
    text = raw.get('output', '') if isinstance(raw, dict) else ''
    failed = status == 'failed' or (isinstance(raw, dict) and raw.get('error'))
    if name == 'run_command':
        code = _exit_code(raw, text)
        if failed and code is None:
            code = 1
        directory = args.get('Cwd') or args.get('cwd') or ags.INNER_WORKSPACE
        return {'type': 'command_execution', 'command': host_view(args.get('CommandLine', ''), workspace),
                'working_directory': host_view(directory, workspace), 'aggregated_output': text,
                'exit_code': code, 'status': 'completed' if not failed and code in (0, None) else 'failed',
                **({'backgrounded': True} if code is None and not failed else {})}
    if name in ('write_file', 'edit_file'):
        return {'type': 'file_change', 'changes': [{'path': host_view(args.get('Path', ''), workspace)}],
                'status': 'failed' if failed else 'completed'}
    path = str(args.get('Path') or '')
    label = 'view_image' if name == 'read_file' and path.lower().endswith(IMAGE_SUFFIXES) else name
    return {'type': 'mcp_tool_call', 'name': 'antigravity_' + label,
            'arguments': {k: host_view(v, workspace) for k, v in args.items()},
            'status': 'failed' if failed else 'completed'}


def project(records, workspace, rc, result):
    """ACP session/update tool calls -> events.jsonl items (Codex item shapes)."""
    rows, done, calls = [], set(), 0
    latest = {}
    for message in records:
        if message.get('method') != 'session/update':
            continue
        update = (message.get('params') or {}).get('update') or {}
        if update.get('sessionUpdate') not in ('tool_call', 'tool_call_update'):
            continue
        ident = update.get('toolCallId')
        state = latest.setdefault(ident, {})
        for key in ('name', 'args', 'rawInput', 'rawOutput', 'status'):
            if update.get(key) is not None:
                state[key] = update[key]
        name = state.get('name')
        if name is not None and name not in ALLOWED_TOOLS:
            raise ValueError(f'Antigravity used a tool outside the case allowlist: {name!r}')
        if state.get('status') in ('completed', 'failed') and ident not in done:
            done.add(ident)
            args = state.get('args') or state.get('rawInput') or {}
            rows.append({'type': 'item.completed', 'item': projected_item(name, args, state.get('rawOutput') or {},
                                                                            state['status'], workspace)})
            calls += 1
    pending = [k for k in latest if k not in done]
    terminal = isinstance(result, dict) and result.get('stopReason') == 'end_turn'
    success = rc == 0 and terminal and calls > 0 and not pending
    if success:
        rows.append({'type': 'turn.completed'})
    return rows, success, calls


def _wire_tool_items(request):
    items = []
    for content in (request.get('request') or {}).get('contents') or []:
        for part in content.get('parts') or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get('functionCall'), dict):
                call = part['functionCall']
                items.append({'type': 'function_call', 'call_id': call.get('id'), 'name': call.get('name'),
                              'arguments': json.dumps(call.get('args'), ensure_ascii=False)})
            elif isinstance(part.get('functionResponse'), dict):
                response = part['functionResponse']
                output = [{'type': 'input_text', 'text': json.dumps(response.get('response'), ensure_ascii=False)}]
                for extra in response.get('parts') or []:
                    data = (extra or {}).get('inlineData') or {}
                    if data.get('mimeType', '').startswith('image/') and data.get('data'):
                        output.append({'type': 'input_image',
                                       'image_url': f"data:{data['mimeType']};base64,{data['data']}"})
                items.append({'type': 'function_call_output', 'call_id': response.get('id'), 'output': output})
    return items


def declared_tools(request):
    """Function names the model was offered in one Cloud Code request."""
    return {declaration.get('name') for tool in (request.get('request') or {}).get('tools') or []
            for declaration in (tool or {}).get('functionDeclarations') or []}


def usage_fields(usage):
    prompt = usage.get('promptTokenCount') or 0
    cached = usage.get('cachedContentTokenCount') or 0
    thoughts = usage.get('thoughtsTokenCount') or 0
    out = output_tokens(usage) or 0
    return {'input_tokens': prompt, 'cached_input_tokens': cached, 'output_tokens': out,
            'reasoning_output_tokens': thoughts, 'total_tokens': prompt + out}


def prepared_rollout(wire: Path, logs: Path, events, budget, terminal):
    """Cloud Code wire counterpart of controlled_harness.prepared_rollout_messages."""
    from .full_trace import extract_tools
    import uuid

    session = str(uuid.uuid4())
    seen, tools, totals = set(), set(), {}
    auxiliary, declared = [], set()
    rollout = logs/'full_rollout.jsonl'
    atomic_bytes(rollout, b'')
    with rollout.open('r+b', buffering=0) as f:
        def add(kind, item, stamp):
            write_stream_bytes(f, (json.dumps({'type': kind, 'timestamp': stamp, 'payload': item}) + '\n').encode())
        add('session_meta', {'id': session, 'source': 'controlled-native Cloud Code wire (verbatim relay)'}, utc_now())
        for directory in sorted(wire.glob('[0-9]*')):
            try:
                receipt = json.loads((directory/'receipt.json').read_text())
            except (OSError, ValueError):
                continue
            if not receipt.get('model_call'):
                continue
            stamp = receipt.get('completed_utc') or utc_now()
            if receipt.get('refused'):
                auxiliary.append({'request': directory.name, 'model': receipt.get('requested_model'),
                                  'request_type': receipt.get('request_type'), 'refused': receipt['refused']})
                continue
            try:
                request = json.loads((directory/'input.body').read_bytes())
            except ValueError:
                continue
            declared.update(declared_tools(request))
            for item in _wire_tool_items(request):
                key = item['type'], item.get('call_id')
                if key in tools:
                    continue
                tools.add(key)
                add('response_item', item, stamp)
            rid, usage = receipt.get('response_id'), receipt.get('usage')
            key = f'{directory.name}:{rid}'
            if not rid or key in seen or not isinstance(usage, dict):
                continue
            seen.add(key)
            fields = usage_fields(usage)
            for k, count in fields.items():
                totals[k] = totals.get(k, 0) + count
            add('token_usage_record', {'session_id': session, 'turn_id': session, 'response_id': rid, 'usage': fields,
                                       'model': receipt.get('requested_model'), 'model_version': receipt.get('model_version'),
                                       'native_usage': usage}, stamp)
    event = {'type': 'turn.completed' if terminal else 'turn.failed'}
    if seen and not budget.unavailable:
        event['usage'] = totals
    if events and events[-1]['type'] == 'turn.completed':
        events[-1] = event
    else:
        events.append(event)
    payload = ''.join(json.dumps(e) + '\n' for e in events)
    for name in ('events.jsonl', 'events.raw.jsonl'):
        atomic_bytes(logs/name, payload.encode())
    return {**extract_tools(rollout, logs), 'source': 'native Cloud Code wire and ACP event projection',
            'requests': len(seen), 'thread_id': session, 'refused_model_calls': auxiliary,
            'model_declared_tools': sorted(declared), 'model_tools_within_allowlist': declared <= ALLOWED_TOOLS}


def relay_model_matches(model):
    return lambda requested: requested == model


def run_prepared_antigravity(*, model, workspace, logs, task, environment, limit, prior_output, runtime, client, digest):
    if model.cli_backend != BACKEND:
        raise ValueError('The Google subscription route is only defined for the pinned Antigravity client')
    # Same audit policy as the Claude native route: private per-case /tmp, kernel-enforced sandbox.
    os.environ['PIXELWORLD_PRIVATE_TMP'] = '1'
    os.environ['PIXELWORLD_SANDBOX_ENFORCED'] = '1'
    out = logs/'native'
    out.mkdir(mode=0o700)
    budget = ResponseUsageBudget(limit, prior_output)
    source = login_state_root(environment)
    # The credential copy lives outside the run directory (HF workers read run dirs).
    state_root = Path(environment.get('PIXELWORLD_AGY_PRIVATE_ROOT') or Path.home()/'.cache'/'pixelworld-agy')
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = Path(tempfile.mkdtemp(prefix='attempt-', dir=state_root))
    try:
        home = state/'home'
        home.mkdir(mode=0o700)
        prepared = prepare_home(home, source)
        command_guard = ags.prepare_command_guard(state / 'command-guard')
        with ExitStack() as stack:
            proxy = stack.enter_context(egress_proxy(log_path=out/'egress.jsonl', allowed_hosts=()))
            relay = stack.enter_context(cloudcode_relay(wire=out/'cloudcode_wire', model=model.model, budget=budget,
                                                        agent_request_types=AGENT_REQUEST_TYPES,
                                                        secrets=lambda: operator_credentials(source),
                                                        host_access_token=lambda: operator_access_token(source, client),
                                                        proxy_url=environment.get('HTTPS_PROXY') or environment.get('https_proxy')))
            bridges = {'proxy': stack.enter_context(cps.relay_bridge(proxy)),
                       'relay': stack.enter_context(cps.relay_bridge(relay))}
            args = ags.sandbox_arguments(client=client, runtime=runtime, workspace=workspace, home=home,
                                         bridges=bridges, ports={'proxy': ags.PROXY_PORT, 'relay': ags.RELAY_PORT},
                                         client_args=client_arguments(), env=ags.sandbox_environment(),
                                         read_only_home=prepared['read_only'], command_guard=command_guard)
            command = cps.sandbox_command(args, state/'sandbox.args')
            launch = {'backend': BACKEND, 'version': VERSION, 'executable_sha256': digest, 'model': model.model,
                      'reasoning_effort': model.reasoning_effort, 'effort_source': 'model slug (agy thinkingLevel)',
                      'interface': 'agy acp (Agent Client Protocol, stdio)', 'agent': AGENT_NAME,
                      'agent_markdown': AGENT_MARKDOWN, 'model_visible_tools': [*AGENT_TOOLS, 'manage_task'],
                      'settings': prepared['settings'], 'login_state_copied': prepared['copied'],
                      'permission_mode': 'always-proceed; whole-process filesystem isolation, no real credentials in client',
                      'auth': 'operator Google AI OAuth held by a fixed-upstream host relay; '
                              'client HOME contains only a non-secret placeholder; native refresh serialized externally',
                      'transport': 'Cloud Code recording relay (--cloud_code_endpoint) + CONNECT allowlist proxy '
                                   f'{sorted(ANTIGRAVITY_HOSTS)} via unix-socket bridges into a network-less sandbox',
                      'sandbox': 'bwrap whole-process (user/pid/net/ipc/uts namespaces; workspace at '
                                 f'{ags.INNER_WORKSPACE} is the only writable host path) + inherited seccomp network guard for '
                                 'agent commands',
                      'private_tmp': 'per-case empty tmpfs, counted as case scratch by the audit',
                      'projection_scratch_aliases': {'/tmp': '.agy-private-tmp',
                          ags.INNER_HOME+'/.gemini/antigravity-cli/brain': '.agy-private-brain'},
                      'bwrap_arguments': args, 'command': command,
                      'first_frame_attached': (workspace/'first_frame.png').is_file(),
                      'first_frame_delivery': 'ACP image content block in the first user message'}
            atomic_json(logs/'native_launch.json', launch)
            driver = AcpDriver(command, out, budget, model.model, model.reasoning_effort, workspace, {'PATH': '/usr/bin:/bin'},
                               secrets=lambda: operator_credentials(source))
            rc, stop, result, info = driver.run(prompt_blocks(workspace, task), pid_file=logs/'agent.pid')
    finally:
        shutil.rmtree(state, ignore_errors=True)
    records = []
    for line in (out/'events.native.jsonl').read_text(errors='replace').splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    events, terminal, calls = project(records, workspace, rc, result)
    if not budget.can_dispatch and stop is None:
        stop = 'token_usage_unavailable' if budget.unavailable else 'output_token_budget_exhausted'
    trace = prepared_rollout(out/'cloudcode_wire', logs, events, budget, terminal and stop is None)
    if not trace['model_tools_within_allowlist']:
        # A model offered extra tools (web search, subagents, ...) is outside the protocol.
        terminal = False
        stop = stop or 'tool_declarations_outside_allowlist'
        trace = prepared_rollout(out/'cloudcode_wire', logs, [e for e in events if e['type'] != 'turn.completed'],
                                 budget, False)
    acp_usage = (result or {}).get('usage') if isinstance(result, dict) else None
    atomic_json(logs/'native_delivery.json', {'terminal_ok': terminal, 'tool_calls': calls,
        'raw_events': 'native/events.native.jsonl', 'stop_reason': stop, 'budget': budget.report(),
        'acp_stop_reason': (result or {}).get('stopReason') if isinstance(result, dict) else None,
        'acp_reported_usage': acp_usage, 'session': info, 'permission_decisions': driver.permissions})
    return rc, stop, budget.report(), trace
