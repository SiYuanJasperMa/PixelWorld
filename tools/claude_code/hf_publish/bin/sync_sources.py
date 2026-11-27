"""Build per-model shadow run roots + live_sources.json for the PixelWorld exporter.

Our matrices put several Claude models in one runs_root per benchmark, but the
public layout (and build_main_suite_status.viewer_href) uses one suite key per
model for newer cohorts, e.g. rbench_common_manipulation_<model>,
physics_iq_720p24fps_glm54.  Each shadow root holds:
  status.json            real status.json filtered to <model>/vision-agent
  <model> -> symlink     to the real <runs_root>/<model>  (read-only use)
  STOP / retry_authorizations.json -> symlinks when present upstream
Nothing in the real run roots is written.
"""
import argparse, json, os, time
from pathlib import Path

BENCH = {  # benchmark dir -> (public suite base, benchmark label, title, output_text)
    'physics_iq':  ('physics_iq_720p24fps', 'physics_iq', 'Physics-IQ Verified · 720p24fps', 'I2V'),
    'rbench':      ('rbench_common_manipulation', 'rbench', 'RBench Common Manipulation', 'I2V'),
    'phygenbench': ('phygenbench_t2v_main720p24fps', 'phygenbench', 'PhyGenBench T2V · 720p24fps', 'T2V'),
    'pcve':        ('pcve_standard720p24fps', 'pcve', 'PCVE-RigidBench · official 720p24fps', 'V2V editing'),
}
TRACK = 'vision-agent'


def atomic(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=1)
    if path.exists() and path.read_text() == text:
        return
    tmp = path.with_name(path.name + '.tmp'); tmp.write_text(text); tmp.replace(path)


def link(target, name):
    if name.is_symlink() and os.readlink(name) == str(target):
        return
    if name.is_symlink():
        name.unlink()
    name.symlink_to(target)


def sync(bundle, runs_roots, models):
    shadow = bundle / 'sources'; shadow.mkdir(parents=True, exist_ok=True)
    records, seen = [], set()
    # Several run roots (e.g. runs_claude for Opus, runs_claude_fable for Fable); the
    # suite key is per (benchmark, model), so the first root that has a model wins.
    for runs, (bench, (base, label, title, output)) in [(r, b) for r in runs_roots for b in BENCH.items()]:
        real = runs / bench
        try:
            status = json.loads((real / 'status.json').read_text())
        except (OSError, ValueError):
            continue
        for model in models:
            if (bench, model) in seen:
                continue
            group = f'{model}/{TRACK}'
            if group not in status.get('groups', {}) or not (real / model / TRACK).is_dir():
                continue  # model not started on this benchmark yet
            seen.add((bench, model))
            key = f'{base}_{model}'
            root = shadow / key; root.mkdir(exist_ok=True)
            link(real / model, root / model)
            for extra in ('STOP', 'retry_authorizations.json'):
                if (real / extra).exists():
                    link(real / extra, root / extra)
                elif (root / extra).is_symlink():
                    (root / extra).unlink()
            atomic(root / 'status.json', {**status, 'groups': {group: status['groups'][group]},
                                          '_shadow_of': str(real)})
            records.append({'key': key, 'runs_root': str(root), 'benchmark': label,
                            'title': f'{title} · {model}', 'output_text': output, 'stage': 'main'})
    atomic(bundle / 'live_sources.json', {'sources': records})
    return records


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--runs', type=Path, nargs='+', required=True,
                   help='one or more run roots, each holding <benchmark>/status.json')
    p.add_argument('--models', nargs='+', default=['claude-opus-5-5', 'claude-fable-5-2'])
    p.add_argument('--interval', type=float, default=0, help='0 = one pass')
    a = p.parse_args()
    while True:
        rows = sync(a.bundle.resolve(), [r.resolve() for r in a.runs], a.models)
        print(json.dumps({'utc': time.time(), 'sources': [r['key'] for r in rows]}), flush=True)
        if not a.interval:
            break
        time.sleep(a.interval)
