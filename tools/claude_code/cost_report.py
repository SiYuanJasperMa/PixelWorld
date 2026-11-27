#!/usr/bin/env python3
"""API-equivalent cost of Claude runs from relay receipts (Opus 5.5 list prices).

usage: cost_report.py [runs_root]   (default $PIXELWORLD_HOME/runs_claude)
Prices ($/MTok): input 4, output 20, cache read 0.20, cache write 5 (5-min) / 8 (1-hour).
The subscription is not billed per token; this is an equivalence estimate only.
"""
import json, glob, sys, os, collections, datetime as dt
P = {'in':4.0,'out':20.0,'cr':0.20,'cw5':5.0,'cw1h':8.0}  # $/MTok
home = os.environ.get('PIXELWORLD_HOME') or os.path.expanduser('~/pixelworld_opus')
root = sys.argv[1] if len(sys.argv)>1 else f'{home}/runs_claude'
cases = collections.OrderedDict()
for f in sorted(glob.glob(f'{root}/*/*/*/case_*/logs/native/messages_wire/*/receipt.json')):
    parts = f[len(root)+1:].split('/'); key = (parts[0], parts[3])
    r = json.load(open(f)); u = r.get('usage') or {}
    c = cases.setdefault(key, collections.Counter())
    cc = u.get('cache_creation') or {}
    w1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
    w5 = (u.get('cache_creation_input_tokens') or 0) - w1
    c['req'] += 1; c['in'] += u.get('input_tokens') or 0; c['out'] += u.get('output_tokens') or 0
    c['cr'] += u.get('cache_read_input_tokens') or 0; c['cw5'] += w5; c['cw1h'] += w1
    m = os.path.getmtime(f); c['t0'] = min(c['t0'] or m, m); c['t1'] = max(c['t1'], m)
tot = collections.Counter()
print(f"{'bench':12} {'case':28} {'req':>4} {'out':>8} {'in':>8} {'cacheR':>9} {'cacheW':>8} {'min':>5} {'$':>7}")
for (b, k), c in cases.items():
    usd = sum(c[x]*P[x] for x in P)/1e6
    mins = (c['t1']-c['t0'])/60
    print(f"{b:12} {k:28} {c['req']:4} {c['out']:8} {c['in']:8} {c['cr']:9} {c['cw5']+c['cw1h']:8} {mins:5.0f} {usd:7.2f}")
    for x in P: tot[x] += c[x]
    tot['usd'] += usd
print(f"TOTAL cases={len(cases)} out={tot['out']} cacheR={tot['cr']} ${tot['usd']:.2f}")
