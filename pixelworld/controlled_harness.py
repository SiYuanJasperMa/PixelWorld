"""Run one case with a locked native client, controlled tools, and a shared budget."""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

from .audit import validate
from .case_tool_mcp import tool_list
from . import claude_process_sandbox as cps
from .messages_responses_proxy import messages_frontend
from .messages_passthrough_relay import messages_relay, responses_usage as messages_usage, wire_items
from .responses_budget_proxy import responses_relay
from .token_budget import ResponseUsageBudget, stop_agent
from .util import atomic_json, sha256, utc_now

VERSION = {'opencode': '1.19.6', 'claude_code': '2.1.304 (Claude Code)', 'antigravity': '1.5.18'}
TOOL_NAMES = {x['name'] for x in tool_list()}


def pinned_client(locks, name):
    lock = json.loads(locks.read_text())['harnesses'][name]
    path = Path(lock['binary'])
    if sha256(path) != lock['sha256'] or lock['version'] != VERSION[name]:
        raise ValueError('Native executable differs from its pin')
    return path, lock['sha256']


def mcp_command(workspace, runtime):
    return [sys.executable, '-m', 'pixelworld.case_tool_mcp', '--workspace', str(workspace), '--runtime', str(runtime)]


def clean_environment(out):
    env = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
           'PYTHONDONTWRITEBYTECODE': '1'}
    for var, folder in {'HOME': 'home', 'XDG_CONFIG_HOME': 'config', 'XDG_DATA_HOME': 'data',
                         'XDG_CACHE_HOME': 'cache', 'TMPDIR': 'tmp'}.items():
        path = out/folder
        path.mkdir()
        env[var] = str(path)
    return env


def open_code(stack, client, out, runtime, workspace, env, endpoint, key, model, effort, context, task):
    conf = Path(env['XDG_CONFIG_HOME'])/'opencode.json'
    command = mcp_command(workspace, runtime)
    atomic_json(conf, {'enabled_providers': ['pixelworld'], 'model': 'pixelworld/'+model,
        'small_model': 'pixelworld/'+model, 'autoupdate': False,
        'permission': {'*':'deny', 'pixelworld_*':'allow'}, 'instructions': [],
        'provider': {'pixelworld': {'npm': '@ai-sdk/openai', 'name': 'PixelWorld pinned Responses',
            'options': {'baseURL': endpoint, 'apiKey': '{env:PIXELWORLD_RELAY_KEY}'},
            'models': {model: {'name': model, 'reasoning': True, 'attachment': True,
                'modalities': {'input': ['text','image'], 'output': ['text']},
                'limit': {'context': context, 'output': 32768}, 'options': {'reasoningEffort': effort},
                'variants': {effort: {'reasoningEffort': effort}}}}}},
        'mcp': {'pixelworld': {'type': 'local', 'command': command, 'enabled': True}}})
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    secret = secrets.token_urlsafe(24)
    env.update(OPENCODE_CONFIG=str(conf), PIXELWORLD_RELAY_KEY=key,
               OPENCODE_SERVER_USERNAME='opencode', OPENCODE_SERVER_PASSWORD=secret)
    env.update({var: '1' for var in ('OPENCODE_DISABLE_AUTOUPDATE', 'OPENCODE_DISABLE_SHARE',
                'OPENCODE_DISABLE_DEFAULT_PLUGINS', 'OPENCODE_DISABLE_PROJECT_CONFIG',
                'OPENCODE_DISABLE_MODELS_FETCH')})
    log = stack.enter_context((out/'server.log').open('wb'))
    server = subprocess.Popen([str(client),'serve','--hostname','127.0.0.1','--port',str(port)],
        cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)
    stack.callback(stop_agent, server)
    auth = 'Basic '+base64.b64encode(('opencode:'+secret).encode()).decode()
    deadline = time.monotonic()+30
    opener = build_opener(ProxyHandler({}))
    while True:
        try:
            with opener.open(Request(url+'/global/health',headers={'Authorization':auth}), timeout=2) as r:
                if r.status == 200:
                    break
        except OSError:
            if server.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Pinned OpenCode server did not become ready')
            time.sleep(.25)
    return ([str(client),'run',task,'--attach',url,'--dir',str(workspace),'--file',
             str(workspace/'first_frame.png'),'--format','json','--model','pixelworld/'+model,
             '--variant',effort], None)


def claude_code(stack, client, out, runtime, workspace, env, endpoint, key, model, effort, task):
    native_key = secrets.token_urlsafe(32)
    allowed = frozenset('mcp__pixelworld__'+x for x in TOOL_NAMES)
    address = stack.enter_context(messages_frontend(responses_base=endpoint, responses_key=key,
        client_key=native_key, model=model, effort=effort, wire=out/'messages_wire', allowed_tools=allowed))
    command = mcp_command(workspace, runtime)
    conf = Path(env['XDG_CONFIG_HOME'])/'mcp.json'
    atomic_json(conf, {'mcpServers': {'pixelworld': {'type':'stdio', 'command': command[0],
        'args':command[1:], 'env':{'PYTHONPATH':env['PYTHONPATH'], 'PYTHONDONTWRITEBYTECODE':'1'}}}})
    env.update(ANTHROPIC_BASE_URL=address, ANTHROPIC_API_KEY=native_key,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_TELEMETRY='1', DISABLE_ERROR_REPORTING='1')
    cmd = [str(client),'-p','--bare','--tools','','--setting-sources','','--strict-mcp-config',
           '--mcp-config',str(conf),'--disable-slash-commands','--no-session-persistence','--model',model,
           '--effort',effort,'--allowedTools',','.join(sorted(allowed)), '--input-format','stream-json',
           '--output-format','stream-json','--verbose']
    image = {'type':'image','source':{'type':'base64','media_type':'image/png',
              'data':base64.b64encode((workspace/'first_frame.png').read_bytes()).decode()}}
    payload = {'type':'user','message':{'role':'user','content':[{'type':'text','text':task}, image]},
               'parent_tool_use_id': None,'session_id':''}
    return cmd, (json.dumps(payload)+'\n').encode()


SUBSCRIPTION_PROVIDER = 'anthropic_subscription'


def claude_code_subscription(stack, client, out, runtime, workspace, env, relay, token, model, effort, task):
    """Pinned Claude Code on the operator's own subscription, through a verbatim relay.

    ``--bare`` is not usable here because it ignores subscription OAuth, so the
    same boundary is set explicitly: a fresh empty HOME/XDG tree, no setting
    sources, no built-in tools, only the case MCP server, no slash commands and
    no session persistence.
    """
    allowed = frozenset('mcp__pixelworld__'+x for x in TOOL_NAMES)
    command = mcp_command(workspace, runtime)
    conf = Path(env['XDG_CONFIG_HOME'])/'mcp.json'
    atomic_json(conf, {'mcpServers': {'pixelworld': {'type':'stdio', 'command': command[0],
        'args':command[1:], 'env':{'PYTHONPATH':env['PYTHONPATH'], 'PYTHONDONTWRITEBYTECODE':'1',
        'PIXELWORLD_CASE_TOOL_TIMEOUT':'600'}}}})
    env.update(ANTHROPIC_BASE_URL=relay, CLAUDE_CODE_OAUTH_TOKEN=token,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_TELEMETRY='1', DISABLE_ERROR_REPORTING='1',
               DISABLE_AUTOUPDATER='1', CLAUDE_CODE_DISABLE_AUTO_MEMORY='1', MCP_TOOL_TIMEOUT='660000')
    env.pop('ANTHROPIC_API_KEY', None)
    cmd = [str(client),'-p','--tools','','--setting-sources','','--strict-mcp-config',
           '--mcp-config',str(conf),'--disable-slash-commands','--no-session-persistence','--model',model,
           '--effort',effort,'--allowedTools',','.join(sorted(allowed)), '--input-format','stream-json',
           '--output-format','stream-json','--verbose']
    return cmd, first_user_message(workspace, task)


def first_user_message(workspace, task):
    content = [{'type':'text','text':task}]
    if (workspace/'first_frame.png').is_file():
        content.append({'type':'image','source':{'type':'base64','media_type':'image/png',
              'data':base64.b64encode((workspace/'first_frame.png').read_bytes()).decode()}})
    payload = {'type':'user','message':{'role':'user','content':content},
               'parent_tool_use_id': None,'session_id':''}
    return (json.dumps(payload)+'\n').encode()


NATIVE_TOOLS = ('Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep')
# Same 10-minute shell limit as the Codex main protocol.
NATIVE_BASH_TIMEOUT_MS = '600000'


def native_claude_args(model, effort):
    tools = ','.join(NATIVE_TOOLS)
    return ['-p','--tools',tools,'--allowedTools',tools,'--permission-mode','bypassPermissions',
            '--setting-sources','','--strict-mcp-config','--disable-slash-commands','--no-session-persistence',
            '--model',model,'--effort',effort,'--input-format','stream-json','--output-format','stream-json',
            '--verbose']


def native_claude_environment():
    return cps.sandbox_environment(extra={
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1', 'DISABLE_TELEMETRY':'1', 'DISABLE_ERROR_REPORTING':'1',
        'DISABLE_AUTOUPDATER':'1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY':'1', 'CLAUDE_CODE_DISABLE_CLAUDE_MDS':'1',
        'CLAUDE_CODE_DISABLE_BUNDLED_SKILLS':'1', 'CLAUDE_CODE_DISABLE_WORKFLOWS':'1', 'CLAUDE_CODE_DISABLE_CRON':'1',
        'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS':'1', 'DISABLE_BUILTIN_AGENTS':'1',
        'BASH_DEFAULT_TIMEOUT_MS':NATIVE_BASH_TIMEOUT_MS, 'BASH_MAX_TIMEOUT_MS':NATIVE_BASH_TIMEOUT_MS})


def claude_code_native_sandboxed(stack, client, runtime, workspace, state, relay, model, effort, task):
    """Pinned Claude Code with exactly its native Bash/Read/Write/Edit/Glob/Grep tools.

    The whole client process tree runs in one bwrap sandbox (see
    ``claude_process_sandbox``): no network except a bridge to this case's
    relay, the workspace as the only writable host directory, a private
    HOME, and only a placeholder credential (the relay injects the real one).
    No MCP servers, setting sources, slash commands, skills, subagents or
    web tools; no session persistence.
    """
    home = state/'sandbox-home'
    home.mkdir(mode=0o700)
    bridge = stack.enter_context(cps.relay_bridge(relay))
    args = cps.sandbox_arguments(client=client, runtime=runtime, workspace=workspace, home=home, bridge=bridge,
                                 port=cps.INNER_PORT, claude_args=native_claude_args(model, effort),
                                 env=native_claude_environment())
    return cps.sandbox_command(args, state/'sandbox.args'), args, first_user_message(workspace, task)


def _drain(source, sink):
    for chunk in iter(lambda: source.read1(65536), b''):
        sink.write(chunk)
        sink.flush()


def execute(command, data, workspace, out, env, budget, deadline, pid_file=None, pipe_output=False):
    """Run the client. ``pipe_output`` hands it pipes instead of the log files, so a
    sandboxed process cannot reopen (and truncate) the host log files via /proc."""
    from threading import Thread
    reason = None
    with (out/'events.native.jsonl').open('wb') as stdout, (out/'stderr.log').open('wb') as stderr:
        if not budget.can_dispatch:
            return 125, 'budget_already_exhausted'
        proc = subprocess.Popen(command,cwd=workspace,env=env,
                    stdout=subprocess.PIPE if pipe_output else stdout,stderr=subprocess.PIPE if pipe_output else stderr,
                    stdin=subprocess.PIPE if data else subprocess.DEVNULL,start_new_session=True)
        drains = [Thread(target=_drain,args=pair,daemon=True) for pair in ((proc.stdout,stdout),(proc.stderr,stderr))] \
            if pipe_output else []
        for thread in drains:
            thread.start()
        try:
            if pid_file is not None:
                pid_file.write_text(str(proc.pid)+'\n')
            if data:
                try:
                    proc.stdin.write(data)
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            end = time.monotonic() + deadline if deadline is not None else None
            while proc.poll() is None:
                if not budget.can_dispatch:
                    reason = 'token_usage_unavailable' if budget.unavailable else 'output_token_budget_exhausted'
                    stop_agent(proc)
                    break
                if end is not None and time.monotonic() >= end:
                    reason = 'smoke_wrapper_deadline'
                    stop_agent(proc)
                    break
                time.sleep(.25)
        finally:
            if proc.poll() is None:
                stop_agent(proc)
            for thread in drains:
                thread.join(timeout=30)
        return proc.returncode, reason


def projected_item(tool, args, text, failed, workspace):
    if tool == 'bash':
        found = re.search(r'exit_code=(-?\d+)', text)
        code = int(found[1]) if found else 1
        return {'type':'command_execution','command':args['command'],'working_directory':str(workspace),
                'aggregated_output':text,'exit_code':code,'status':'completed' if code == 0 else 'failed'}
    if tool in ('write','edit'):
        return {'type':'file_change','changes':[{'path':args['path']}], 'status':'failed' if failed else 'completed'}
    return {'type':'mcp_tool_call','name':'pixelworld_'+tool,'arguments':args}


IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.gif')


def host_view(value, workspace):
    """Map sandbox paths in a projected value to host paths (raw native events stay verbatim)."""
    if not isinstance(value, str):
        return value
    return re.sub(r'(?<![\w./-])'+re.escape(cps.INNER_WORKSPACE)+r'(?=/|\b|$)', str(workspace), value)


def native_projected_item(tool, args, text, failed, workspace):
    if tool == 'Bash':
        found = re.search(r'Exit code (-?\d+)', text) if failed else None
        code = (int(found[1]) if found else 1) if failed else 0
        return {'type':'command_execution','command':host_view(args.get('command',''),workspace),
                'working_directory':str(workspace),'aggregated_output':text,'exit_code':code,
                'status':'completed' if code == 0 else 'failed'}
    if tool in ('Write','Edit'):
        return {'type':'file_change','changes':[{'path':host_view(args.get('file_path',''),workspace)}],
                'status':'failed' if failed else 'completed'}
    path = str(args.get('file_path') or args.get('path') or '')
    name = 'view_image' if tool == 'Read' and path.lower().endswith(IMAGE_SUFFIXES) else tool.lower()
    return {'type':'mcp_tool_call','name':'claude_code_'+name,
            'arguments':{k:host_view(v,workspace) for k,v in args.items()},'status':'failed' if failed else 'completed'}


def project(native, backend, workspace, rc, tool_mode='mcp'):
    rows, pending, calls = [], {}, 0
    terminal = False
    for event in native:
        if backend == 'opencode':
            if event.get('type') == 'error':
                return rows, False, calls
            if event['type'] in ('step_finish','text'):
                terminal = True
            if event['type'] != 'tool_use':
                continue
            part = event['part']
            name, state = part['tool'], part['state']
            tool = name.removeprefix('pixelworld_')
            if name == tool or tool not in TOOL_NAMES:
                raise ValueError('OpenCode used a tool outside the case allowlist')
            item = projected_item(tool,state.get('input') or {},str(state.get('output','')),
                                  state.get('status') != 'completed',workspace)
            rows.append({'type':'item.completed','item':item})
            calls += 1
        else:
            if event['type'] == 'result':
                terminal = event.get('subtype') == 'success' and not event.get('is_error')
            for block in (event.get('message') or {}).get('content',[]):
                if not isinstance(block,dict):
                    continue
                if block['type'] == 'tool_use':
                    if tool_mode == 'native':
                        tool = block['name']
                        if tool not in NATIVE_TOOLS:
                            raise ValueError('Claude Code used a tool outside the native allowlist')
                    else:
                        tool = block['name'].removeprefix('mcp__pixelworld__')
                        if tool == block['name'] or tool not in TOOL_NAMES:
                            raise ValueError('Claude Code used a tool outside the case allowlist')
                    pending[block['id']] = (tool,block['input'])
                if block['type'] == 'tool_result':
                    tool,args = pending.pop(block['tool_use_id'])
                    content = block.get('content','')
                    text = content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content
                                                                             if isinstance(x,dict))
                    shape = native_projected_item if tool_mode == 'native' else projected_item
                    rows.append({'type':'item.completed','item':shape(tool,args,text,block.get('is_error'),workspace)})
                    calls += 1
    success = rc == 0 and terminal and calls > 0 and not pending
    if success:
        rows.append({'type':'turn.completed'})
    return rows, success, calls


def prepared_rollout(wire, logs, events, budget, terminal):
    """Project unique Responses usage/tool records while preserving the raw wire."""
    from .full_trace import extract_tools
    import uuid

    session = str(uuid.uuid4())
    seen, tools, totals = set(), set(), {}
    rollout = logs/'full_rollout.jsonl'
    with rollout.open('w') as f:
        def add(kind, item, time):
            f.write(json.dumps({'type':kind,'timestamp':time,'payload':item})+'\n')
        add('session_meta',{'id':session,'source':'controlled-native Responses wire'},utc_now())
        for directory in sorted(wire.glob('[0-9]*')):
            receipt = json.loads((directory/'receipt.json').read_text())
            stamp = receipt['completed_utc']
            request = json.loads((directory/'input.json').read_text())
            for item in request.get('input', []):
                if not isinstance(item, dict) or item.get('type') not in ('function_call','function_call_output'):
                    continue
                key = item['type'],item.get('call_id')
                if key in tools:
                    continue
                tools.add(key)
                add('response_item',item,stamp)
            rid, usage = receipt['response_id'],receipt['usage']
            if not rid or rid in seen or not isinstance(usage,dict):
                continue
            seen.add(rid)
            fields = {k:usage[k]for k in ('input_tokens','output_tokens','total_tokens') if type(usage.get(k)) is int}
            for k,pair in {'cached_input_tokens':('input_tokens_details','cached_tokens'),
                           'reasoning_output_tokens':('output_tokens_details','reasoning_tokens')}.items():
                count = (usage.get(pair[0]) or {}).get(pair[1])
                if type(count) is int:
                    fields[k] = count
            for k,count in fields.items():
                totals[k] = totals.get(k,0)+count
            add('token_usage_record',{'session_id':session,'turn_id':session,'response_id':rid,'usage':fields},stamp)
    event = {'type':'turn.completed' if terminal else 'turn.failed'}
    if len(seen) and not budget.unavailable:
        event['usage'] = totals
    if events and events[-1]['type'] == 'turn.completed':
        events[-1] = event
    else:
        events.append(event)
    payload = ''.join(json.dumps(e)+'\n' for e in events)
    for name in ('events.jsonl','events.raw.jsonl'):
        (logs/name).write_text(payload)
    return {**extract_tools(rollout,logs),'source':'native request/response wire and event projection',
            'requests':len(seen),'thread_id':session}


def prepared_rollout_messages(wire, logs, events, budget, terminal):
    """Messages-wire counterpart of prepared_rollout (subscription route)."""
    from .full_trace import extract_tools
    import uuid

    session = str(uuid.uuid4())
    seen, tools, totals = set(), set(), {}
    rollout = logs/'full_rollout.jsonl'
    with rollout.open('w') as f:
        def add(kind, item, time):
            f.write(json.dumps({'type':kind,'timestamp':time,'payload':item})+'\n')
        add('session_meta',{'id':session,'source':'controlled-native Messages wire (verbatim relay)'},utc_now())
        for directory in sorted(wire.glob('[0-9]*')):
            receipt = json.loads((directory/'receipt.json').read_text())
            if not receipt.get('model_call'):
                continue
            stamp = receipt.get('completed_utc') or utc_now()
            try:
                request = json.loads((directory/'input.body').read_bytes())
            except ValueError:
                continue
            for item in wire_items(request):
                key = item['type'],item.get('call_id')
                if key in tools:
                    continue
                tools.add(key)
                add('response_item',item,stamp)
            rid, usage = receipt.get('response_id'),receipt.get('usage')
            if not rid or rid in seen or not isinstance(usage,dict):
                continue
            seen.add(rid)
            shaped = messages_usage(usage)
            fields = {'input_tokens':shaped['input_tokens'],'output_tokens':shaped['output_tokens'],
                      'total_tokens':shaped['total_tokens'],
                      'cached_input_tokens':shaped['input_tokens_details']['cached_tokens'],
                      'cache_write_input_tokens':shaped['input_tokens_details']['cache_write_tokens']}
            for k,count in fields.items():
                totals[k] = totals.get(k,0)+count
            add('token_usage_record',{'session_id':session,'turn_id':session,'response_id':rid,'usage':fields,
                                      'native_usage':usage},stamp)
    event = {'type':'turn.completed' if terminal else 'turn.failed'}
    if len(seen) and not budget.unavailable:
        event['usage'] = totals
    if events and events[-1]['type'] == 'turn.completed':
        events[-1] = event
    else:
        events.append(event)
    payload = ''.join(json.dumps(e)+'\n' for e in events)
    for name in ('events.jsonl','events.raw.jsonl'):
        (logs/name).write_text(payload)
    return {**extract_tools(rollout,logs),'source':'native Messages wire and event projection',
            'requests':len(seen),'thread_id':session,'reasoning_output_tokens':'unavailable'}


def run_prepared(*, model, workspace, logs, task, environment, limit, prior_output):
    """Execute a case staged by the standard benchmark runner, not a prior delivery."""
    runtime = Path(environment['PIXELWORLD_CONTROLLED_RUNTIME']).resolve(strict=True)
    locks = Path(environment['PIXELWORLD_CONTROLLED_LOCKS'])
    client,digest = pinned_client(locks,model.cli_backend)
    if model.provider.type == 'google_subscription':
        from .antigravity_harness import run_prepared_antigravity
        return run_prepared_antigravity(model=model, workspace=workspace, logs=logs, task=task,
            environment=environment, limit=limit, prior_output=prior_output, runtime=runtime,
            client=client, digest=digest)
    if model.provider.type == SUBSCRIPTION_PROVIDER:
        return run_prepared_subscription(model=model, workspace=workspace, logs=logs, task=task,
            environment=environment, limit=limit, prior_output=prior_output, runtime=runtime,
            client=client, digest=digest)
    upstream = model.provider.base_url.rstrip('/')+'/responses'
    catalog = json.loads(Path(model.provider.model_catalog_json).read_text())['models']
    item = next(x for x in catalog if x.get('slug') == model.model)
    context = item['context_window']
    if type(context) is not int or context <= 0:
        raise ValueError('Invalid frozen context window')
    key = environment[model.provider.env_key]
    out = logs/'native'
    out.mkdir(mode=0o700)
    state = workspace.parent/'empty-codex-home'/'controlled'
    state.mkdir(mode=0o700)
    env = clean_environment(state)
    budget = ResponseUsageBudget(limit,prior_output)
    relay_key = secrets.token_urlsafe(32)
    with ExitStack() as stack:
        endpoint = stack.enter_context(responses_relay(upstream=upstream,upstream_key=key,
            client_key=relay_key,model=model.model,effort=model.reasoning_effort,wire=out/'responses_wire',budget=budget))
        base = (stack,client,out,runtime,workspace,env,endpoint,relay_key,model.model,model.reasoning_effort)
        command,data = (open_code(*base,context,task) if model.cli_backend=='opencode' else claude_code(*base,task))
        atomic_json(logs/'native_launch.json',{'backend':model.cli_backend,'version':VERSION[model.cli_backend],
                    'executable_sha256':digest,'model':model.model,'command':command,'context_window':context})
        rc,stop = execute(command,data,workspace,out,env,budget,None,pid_file=logs/'agent.pid')
    records = [json.loads(l) for l in (out/'events.native.jsonl').read_text().splitlines() if l.strip()]
    events,terminal,calls = project(records,model.cli_backend,workspace,rc)
    if not budget.can_dispatch and stop is None:
        stop = 'token_usage_unavailable' if budget.unavailable else 'output_token_budget_exhausted'
    trace = prepared_rollout(out/'responses_wire',logs,events,budget,terminal and stop is None)
    if stop == 'token_usage_unavailable':
        from .responses_budget_proxy import transport_failure_evidence
        evidence = transport_failure_evidence(out/'responses_wire', model=model.model, effort=model.reasoning_effort)
        if evidence:
            trace['transport_failure'] = evidence
    atomic_json(logs/'native_delivery.json',{'terminal_ok':terminal,'tool_calls':calls,'raw_events':'native/events.native.jsonl',
                'stop_reason':stop,'budget':budget.report()})
    return rc,stop,budget.report(),trace


def host_subscription_token(min_seconds=300, credentials=None):
    """Current access token of the host's own Claude Code (Max plan) login.

    Cases run under an empty HOME, so they cannot see the host login; the
    launcher hands over the live OAuth access token instead. If it expires
    within ``min_seconds`` the host client is invoked once so it refreshes
    the stored login itself. The value is never logged.
    """
    import subprocess, time
    path = Path(credentials or Path.home()/'.claude'/'.credentials.json')
    def read():
        auth = json.loads(path.read_text())['claudeAiOauth']
        return auth['accessToken'], auth['expiresAt']/1000 - time.time()
    token, left = read()
    if left < min_seconds:
        import fcntl
        with open(path.parent/'.pixelworld_refresh.lock','a') as lock:   # one refresher at a time
            fcntl.flock(lock, fcntl.LOCK_EX)
            token, left = read()
            if left < min_seconds:
                subprocess.run([str(Path.home()/'.local/bin/claude'),'-p','ok','--model','haiku','--max-turns','1'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=120)
                token, left = read()
        if left < 30:
            raise RuntimeError('host Claude Code login could not be refreshed')
    return token


def subscription_tool_mode(model):
    return getattr(model.provider, 'tool_mode', None) or 'native'


def run_prepared_subscription(*, model, workspace, logs, task, environment, limit, prior_output,
                              runtime, client, digest):
    if model.cli_backend != 'claude_code':
        raise ValueError('The subscription route is only defined for the pinned Claude Code client')
    mode = subscription_tool_mode(model)
    if mode == 'native':
        # The native sandbox gives each case a private empty /tmp; the audit in this
        # run-case process treats it as case scratch (see audit.private_tmp_granted).
        os.environ['PIXELWORLD_PRIVATE_TMP'] = '1'
        os.environ['PIXELWORLD_SANDBOX_ENFORCED'] = '1'
    token = environment.get(model.provider.env_key) or host_subscription_token
    upstream = model.provider.base_url or 'https://api.anthropic.com'
    out = logs/'native'
    out.mkdir(mode=0o700)
    state = workspace.parent/'empty-codex-home'/'controlled'
    state.mkdir(mode=0o700)
    env = clean_environment(state)
    budget = ResponseUsageBudget(limit,prior_output)
    with ExitStack() as stack:
        if mode == 'native':
            # The real token stays in this process: the relay injects it and the
            # sandboxed client only holds a placeholder.
            relay = stack.enter_context(messages_relay(wire=out/'messages_wire', model=model.model,
                effort=model.reasoning_effort, budget=budget, upstream=upstream, upstream_authorization=token))
            command,sandbox_args,data = claude_code_native_sandboxed(stack,client,runtime,workspace,state,relay,
                                                                     model.model,model.reasoning_effort,task)
            env = {'PATH':'/usr/bin:/bin'}
            details = {'tool_mode':'native','private_tmp':'per-case empty tmpfs, counted as case scratch by the audit','tools':list(NATIVE_TOOLS),
                       'auth':'operator Claude subscription; OAuth token injected by the relay outside the '
                              'sandbox (client holds a placeholder; value not recorded)',
                       'transport':'verbatim Messages relay via unix-socket bridge into a network-less sandbox',
                       'sandbox':'bwrap whole-process (user/pid/net/ipc/uts namespaces; workspace at '
                                 f'{cps.INNER_WORKSPACE} is the only writable host path)',
                       'bash_timeout_ms':int(NATIVE_BASH_TIMEOUT_MS),'bwrap_arguments':sandbox_args}
        else:
            relay = stack.enter_context(messages_relay(wire=out/'messages_wire', model=model.model,
                effort=model.reasoning_effort, budget=budget, upstream=upstream))
            command,data = claude_code_subscription(stack,client,out,runtime,workspace,env,relay,token,
                                                    model.model,model.reasoning_effort,task)
            details = {'tool_mode':'mcp','auth':'operator Claude subscription (OAuth token, value not recorded)',
                       'transport':'verbatim Messages relay'}
        launch = {'backend':model.cli_backend,'version':VERSION[model.cli_backend],
                  'executable_sha256':digest,'model':model.model,'reasoning_effort':model.reasoning_effort,
                  **details,'upstream':upstream,'command':command,
                  'first_frame_attached':(workspace/'first_frame.png').is_file()}
        atomic_json(logs/'native_launch.json',launch)
        rc,stop = execute(command,data,workspace,out,env,budget,None,pid_file=logs/'agent.pid',
                          pipe_output=mode == 'native')
    records = [json.loads(l) for l in (out/'events.native.jsonl').read_text().splitlines() if l.strip()]
    events,terminal,calls = project(records,model.cli_backend,workspace,rc,tool_mode=mode)
    if not budget.can_dispatch and stop is None:
        stop = 'token_usage_unavailable' if budget.unavailable else 'output_token_budget_exhausted'
    trace = prepared_rollout_messages(out/'messages_wire',logs,events,budget,terminal and stop is None)
    atomic_json(logs/'native_delivery.json',{'terminal_ok':terminal,'tool_calls':calls,'raw_events':'native/events.native.jsonl',
                'stop_reason':stop,'budget':budget.report(),'tool_mode':mode})
    return rc,stop,budget.report(),trace


def run(args):
    client,digest = pinned_client(args.locks,args.backend)
    source = args.source.resolve(strict=True)
    manifest = json.loads((source/'run_manifest.json').read_text())
    spec = manifest['media_contract']
    if (manifest['status'] != 'complete' or manifest['validation'].get('pass') is not True or
        (spec['task_type'],spec['width'],spec['height'],spec['fps'],spec['frames']) != ('i2v',1280,720,24,120) or
        manifest['model'] != args.model or manifest['reasoning_effort'] != args.effort):
        raise ValueError('Acceptance requires a validated standard Physics-IQ case with the same model and effort')
    snapshot = source/'input_snapshot'
    if snapshot.resolve() != snapshot or any(f.is_symlink() for f in snapshot.rglob('*')):
        raise ValueError('Only normal files may enter the controlled case')
    key = os.environ[args.api_key_env]
    task = (source/'task_prompt.txt').read_text()
    out = args.output.absolute()
    if out.resolve() != out or out.is_relative_to(source):
        raise ValueError('Choose a new, separate cloud output directory')
    os.umask(0o077)
    out.mkdir(exist_ok=False)
    workspace = out/'workspace'
    shutil.copytree(snapshot,workspace)
    (workspace/'.tmp').mkdir(exist_ok=True)
    image = sha256(workspace/'first_frame.png')
    if image != manifest['input_image_sha256']:
        raise ValueError('Input snapshot does not match the accepted source hash')
    (out/'task_prompt.txt').write_text(task)
    env = clean_environment(out)
    budget = ResponseUsageBudget(args.budget,args.prior_output)
    relay_key = secrets.token_urlsafe(32)
    launch = {'backend':args.backend,'version':VERSION[args.backend],'executable_sha256':digest,
              'model':args.model,'reasoning_effort':args.effort,'source_case':manifest['case_id'],
              'source_manifest_sha256':sha256(source/'run_manifest.json'),'input_sha256':image,
              'media_contract':spec,'scope':'one-case acceptance, not a full cohort','started_utc':utc_now()}
    atomic_json(out/'launch.json',launch)
    with ExitStack() as stack:
        endpoint = stack.enter_context(responses_relay(upstream=args.upstream,upstream_key=key,
            client_key=relay_key,model=args.model,effort=args.effort,wire=out/'responses_wire',budget=budget))
        base = (stack,client,out,args.runtime.resolve(strict=True),workspace,env,endpoint,relay_key,args.model,args.effort)
        command,data = open_code(*base,args.context_window,task) if args.backend=='opencode' else claude_code(*base,task)
        rc,stop = execute(command,data,workspace,out,env,budget,args.timeout)
    native = [json.loads(l) for l in (out/'events.native.jsonl').read_text().splitlines() if l.strip()] if (out/'events.native.jsonl').is_file() else []
    projected,terminal,calls = project(native,args.backend,workspace,rc)
    file = out/'audit_projection.jsonl'
    file.write_text(''.join(json.dumps(e)+'\n' for e in projected))
    validation = validate(workspace,file,rc if rc is not None else 124,'vision-agent',require_reports=False,media=spec)
    wire = [json.loads(f.read_text()) for f in sorted((out/'responses_wire').glob('*/receipt.json'))]
    ids = [row['response_id'] for row in wire if row['response_id']]
    valid = terminal and validation['pass'] and budget.can_dispatch and bool(ids) and len(ids) == len(set(ids))
    receipt = {**launch,'exit_status':rc,'stop_reason':stop,'validation':validation,'terminal_ok':terminal,
               'native_tool_calls':calls,'responses_count':len(wire),'budget':budget.report(),
               'video_smoke_pass':valid,'matrix_admitted':False,'completed_utc':utc_now()}
    atomic_json(out/'receipt.json',receipt)
    print(json.dumps({'video_smoke_pass':valid,'frames':validation['frames'],'budget':budget.report()}),flush=True)
    return 0 if valid else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=('opencode','claude_code'),required=True)
    for name in ('locks','runtime','source','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--upstream',required=True)
    p.add_argument('--model',required=True)
    p.add_argument('--effort',default='high')
    p.add_argument('--api-key-env',default='PIXELWORLD_API_KEY')
    p.add_argument('--context-window',type=int,default=272000)
    p.add_argument('--budget',type=int,default=1_000_000)
    p.add_argument('--prior-output',type=int,default=0)
    p.add_argument('--timeout',type=float,default=1800)
    raise SystemExit(run(p.parse_args()))


if __name__ == '__main__':
    main()
