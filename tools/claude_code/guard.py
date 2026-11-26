#!/usr/bin/env python3
"""Quota + memory + token guard: pauses new dispatch (running cases finish) via scheduling_limits.json.

Reserve: stop dispatch at >=90% weekly (all models) for every model, and >=90% Fable for Fable
(10% of the weekly subscription quota stays reserved for the operator). Unknown quota, a failed
host OAuth refresh (host_subscription_token) or < 5 GB available memory also pause dispatch.

Environment: PIXELWORLD_HOME (default $HOME/pixelworld_opus), PIXELWORLD_REPO, PIXELWORLD_CONFIGS,
CLAUDE_HOST_BIN (host Claude Code used for `/usage`, default ~/.local/bin/claude).
"""
import json, re, subprocess, time, os, datetime as dt
HOME = os.environ.get('PIXELWORLD_HOME') or os.path.expanduser('~/pixelworld_opus')
REPO = os.environ.get('PIXELWORLD_REPO') or f'{HOME}/PixelWorld'
CFG = os.environ.get('PIXELWORLD_CONFIGS') or f'{HOME}/configs_claude'
CLAUDE = os.environ.get('CLAUDE_HOST_BIN') or os.path.expanduser('~/.local/bin/claude')
LIM = f'{CFG}/scheduling_limits.json'
LOG = f'{HOME}/guard.log'
MAINS = ['physics_iq_main_opus.json','rbench_main_opus.json','phygenbench_main_opus.json','pcve_main_opus.json','physics_iq_main_fable.json','rbench_main_fable.json','rbench_demo_fable.json','phygenbench_demo_fable.json','pcve_demo_fable.json']
CAP, MEM_MIN_GB = 90, 5
def usage():
    out = subprocess.run([CLAUDE, '-p', '/usage'], capture_output=True,
                         text=True, timeout=120, cwd='/tmp').stdout
    a = re.search(r'Current week \(all models\): (\d+)% used', out); f = re.search(r'Current week \(Fable\): (\d+)% used', out)
    return (int(a.group(1)) if a else None), (int(f.group(1)) if f else None)
def mem_gb():
    for l in open('/proc/meminfo'):
        if l.startswith('MemAvailable'): return int(l.split()[1]) / 1048576
while True:
    try:
        a, f = usage()
    except Exception as e:
        a, f = None, None
    m = mem_gb()
    tok = subprocess.run([f'{REPO}/.venv/bin/python','-c',
        'from pixelworld.controlled_harness import host_subscription_token as h; h()'],
        cwd=REPO, capture_output=True, timeout=200).returncode == 0
    paused = set()
    if a is None or a >= CAP: paused |= {'claude-opus-5-5', 'claude-fable-5-2'}   # unknown quota -> hold
    if f is None or f >= CAP: paused.add('claude-fable-5-2')
    if not tok: paused |= {'claude-opus-5-5', 'claude-fable-5-2'}
    if m < MEM_MIN_GB: paused |= {'claude-opus-5-5', 'claude-fable-5-2'}
    s = json.load(open(LIM))
    s['paused_models_by_config'] = {c: sorted(paused) for c in MAINS}
    tmp = LIM + '.tmp'; json.dump(s, open(tmp, 'w'), indent=2); os.replace(tmp, LIM)
    with open(LOG, 'a') as g:
        g.write(f"{dt.datetime.utcnow():%Y-%m-%d %H:%M}Z all={a}% fable={f}% memavail={m:.1f}G token_ok={tok} paused={sorted(paused)}\n")
    time.sleep(600 if (m >= MEM_MIN_GB and tok) else 60)
