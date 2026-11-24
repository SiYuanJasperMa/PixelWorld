"""Publish the Video-MME-v2 pixel-input QA smoke page."""
from __future__ import annotations
import base64, hashlib, json, re, subprocess
from pathlib import Path
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT.parent / 'videomme_v2/cases'
OUT = ROOT / 'runs/bench_codex_20261109/videomme_v2_smoke_site'
LABELS = {'1': 'L1', '2': 'L2', '3': 'L3'}

def usage(path: Path) -> dict:
    total = {'input': 0, 'cached': 0, 'output': 0, 'reasoning': 0}
    if not path.is_file():
        return total
    # One codex exec can emit duplicate turn.completed events with cumulative usage;
    # keep the last (largest) snapshot instead of summing them.
    for line in path.read_text(errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('type') != 'turn.completed':
            continue
        u = row.get('usage') or {}
        total = {
            'input': int(u.get('input_tokens') or 0),
            'cached': int(u.get('cached_input_tokens') or 0),
            'output': int(u.get('output_tokens') or 0),
            'reasoning': int(u.get('reasoning_output_tokens') or 0),
        }
    return total

def thumbs(case: Path, name: str) -> list[str]:
    frames = sorted((case / 'workspace/frames').glob('*.png'))
    if not frames:
        return []
    picks = [frames[round(i * (len(frames) - 1) / 7)] for i in range(8)]
    assets = OUT / 'assets' / name
    assets.mkdir(parents=True, exist_ok=True)
    out = []
    for i, frame in enumerate(picks):
        target = assets / f'{i}.jpg'
        if not target.exists():
            im = Image.open(frame).convert('RGB')
            im.thumbnail((480, 480))
            im.save(target, quality=82)
        out.append(f'assets/{name}/{i}.jpg')
    return out

def collect() -> list[dict]:
    rows = []
    for folder in sorted(CASES.iterdir()):
        if not folder.is_dir() or folder.name.startswith('_'):
            continue
        gt_path = folder / 'ground_truth.json'
        if not gt_path.is_file():
            continue
        gt = json.loads(gt_path.read_text())
        if gt.get('second_head') not in ('Action & Motion', 'Physical World Reasoning'):
            continue
        answer_path = folder / 'workspace/answer.txt'
        answer = evidence = ''
        if answer_path.is_file():
            text = answer_path.read_text(errors='replace')
            m = re.search(r'ANSWER:\s*([A-H])', text, re.I)
            answer = m.group(1).upper() if m else ''
            e = re.search(r'EVIDENCE:\s*(.+)', text, re.S)
            evidence = e.group(1).strip() if e else ''
        q = (folder / 'workspace/question.txt').read_text(errors='replace')
        meta_path = folder / 'logs/meta.json'
        model_name = ''
        if meta_path.is_file():
            try:
                model_name = json.loads(meta_path.read_text()).get('model', '').split('/')[-1]
            except ValueError:
                model_name = ''
        u = usage(folder / 'logs/events.jsonl')
        n_frames = len(sorted((folder / 'workspace/frames').glob('*.png')))
        rows.append({
            'id': folder.name, 'level': str(gt.get('level')), 'head': gt.get('third_head'),
            'answer': gt.get('answer'), 'model': answer, 'correct': bool(answer) and answer == gt.get('answer'),
            'evidence': evidence, 'question': q, 'usage': u, 'model_name': model_name, 'n_frames': n_frames,
            'frames': thumbs(folder, folder.name),
            'events': len((folder / 'logs/events.jsonl').read_text(errors='replace').splitlines()) if (folder / 'logs/events.jsonl').is_file() else 0,
        })
    return rows

def page(rows: list[dict]) -> str:
    n = len(rows); done = sum(1 for r in rows if r['model'])
    correct = sum(1 for r in rows if r['correct'])
    tok = sum(r['usage']['output'] for r in rows)
    cached = sum(r['usage']['cached'] for r in rows)
    inp = sum(r['usage']['input'] for r in rows)
    models = ' / '.join(sorted({r['model_name'] for r in rows if r['model_name']})) or '—'
    body = []
    for r in rows:
        strip = ''.join(f'<img src="{p}" alt="frame">' for p in r['frames'])
        status = ('<span class="ok">✓ 正确</span>' if r['correct'] else
                  ('<span class="bad">✗ 错误</span>' if r['model'] else '<span class="wait">运行中</span>'))
        body.append(f'''<article class="case">
<div class="head"><h3>{r['id']}</h3><span class="pill">{LABELS.get(r['level'], r['level'])}</span>
<span class="pill">{r['head']}</span>{status}</div>
<div class="strip">{strip}</div>
<div class="qa"><div><h4>题目</h4><pre>{r['question']}</pre></div>
<div><h4>结果</h4><p>标准答案 <b>{r['answer']}</b> · 模型答案 <b>{r['model'] or '—'}</b></p>
<p class="ev">{r['evidence'] or '尚无证据'}</p>
<p class="meta">{r['n_frames']} 帧（1 fps）· output {r['usage']['output']:,} tok · reasoning {r['usage']['reasoning']:,} · 事件 {r['events']}</p></div></div>
</article>''')
    return f'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Video-MME-v2 · Pixel Input QA Smoke</title>
<style>
:root{{--bg:#0b0f14;--panel:#141b24;--line:#2b3745;--text:#edf2f7;--muted:#a0adbc;--green:#b8ed94;--red:#ffaaa0;--blue:#94ccff;--amber:#f4cb91}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,sans-serif}}
main{{max-width:1100px;margin:auto;padding:32px 24px 64px}}a{{color:var(--blue)}}
h1{{font-size:34px;margin:8px 0 10px}}h2{{margin:32px 0 12px}}.muted{{color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;margin:16px 0}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}
.kpi{{background:#101720;border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi strong{{display:block;font-size:24px}}.kpi span{{font-size:11px;color:var(--muted)}}
.case{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin:18px 0}}
.head{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.head h3{{margin:0;font-size:20px}}
.pill{{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--muted)}}
.ok{{color:var(--green);font-weight:700}}.bad{{color:var(--red);font-weight:700}}.wait{{color:var(--amber);font-weight:700}}
.strip{{display:grid;grid-template-columns:repeat(8,1fr);gap:6px;margin:12px 0}}
.strip img{{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;background:#070b10}}
.qa{{display:grid;grid-template-columns:1.2fr 1fr;gap:18px}}pre{{white-space:pre-wrap;font:12px/1.6 ui-monospace,monospace;color:#c8d6e5;margin:0}}
h4{{margin:0 0 6px;font-size:13px;color:var(--muted)}}.ev{{font-size:13px}}.meta{{font-size:12px;color:var(--muted)}}
@media(max-width:800px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.qa{{grid-template-columns:1fr}}.strip{{grid-template-columns:repeat(4,1fr)}}}}
</style>
<main>
<p><a href="/pixelworld/index.html">← PixelWorld 总览</a></p>
<section class="hero"><h1>Video-MME-v2 · 像素输入 QA</h1>
<p class="muted">模型：{models}。纯 Text track：只给 PNG 帧，不给 mp4、图片附件、音频和字幕；agent 必须用代码读 RGB 后作答。本页只包含 Action & Motion 与 Physical World Reasoning 两个子维度。</p></section>
<div class="kpis">
<div class="kpi"><strong>{done}/{n}</strong><span>已作答</span></div>
<div class="kpi"><strong>{correct}/{n}</strong><span>正确（随机基线 12.5%）</span></div>
<div class="kpi"><strong>{tok:,}</strong><span>output tokens</span></div>
<div class="kpi"><strong>{inp:,}</strong><span>input tokens（cached {cached:,}）</span></div>
</div>
<div class="card"><b>统一配置</b><p class="muted">模型 {models}；1 fps 均匀采样；画布保持首帧宽高比、短边 720（16:9 → 1280×720）；每题帧数 = 视频秒数；8 选项 text-only；禁用 view_image 与网络。</p></div>
<h2>逐题结果</h2>
{''.join(body)}
<p class="muted">样本量很小，仅用于验证 pipeline 与可行性，不作为 benchmark 结论。</p>
</main></html>'''

def publish(html: str, assets: Path) -> dict:
    fill = subprocess.run(['git','credential','fill'], input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True, timeout=20)
    creds = dict(x.split('=',1) for x in fill.stdout.splitlines() if '=' in x)
    s = requests.Session(); s.trust_env = False
    s.headers.update({'Authorization':'Bearer '+creds['password'],'Accept':'application/vnd.github+json'})
    base = 'https://api.github.com/repos/SiyuanMaCS/SiyuanMaCS.github.io'; branch = 'pixelworld-pages'
    def call(method, path, **kw):
        r = s.request(method, base+path, timeout=30, **kw); r.raise_for_status(); return r.json()
    head = call('GET','/git/ref/heads/'+branch)['object']['sha']
    tree = call('GET','/git/commits/'+head)['tree']['sha']
    entries = []
    files = {'pixelworld/videomme_v2_smoke/index.html': html.encode()}
    for path in sorted(assets.rglob('*.jpg')):
        files['pixelworld/videomme_v2_smoke/'+path.relative_to(assets).as_posix()] = path.read_bytes()
    for path, data in files.items():
        blob = call('POST','/git/blobs', json={'content':base64.b64encode(data).decode(),'encoding':'base64'})
        entries.append({'path':path,'mode':'100644','type':'blob','sha':blob['sha']})
    new = call('POST','/git/trees', json={'base_tree':tree,'tree':entries})['sha']
    commit = call('POST','/git/commits', json={'message':'Publish Video-MME-v2 pixel QA smoke','tree':new,'parents':[head]})['sha']
    call('PATCH','/git/refs/heads/'+branch, json={'sha':commit,'force':False})
    return {'commit':commit,'files':len(files)}

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    rows = collect()
    html = page(rows)
    (OUT/'index.html').write_text(html)
    print(json.dumps({'cases':len(rows),'answered':sum(1 for r in rows if r['model']),'correct':sum(1 for r in rows if r['correct']),**publish(html, OUT)}))
