"""Draw measured cost/score points; never extrapolate missing settings or scores."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from pixelworld.util import atomic_json, utc_now

BENCHMARKS = {'physics_iq': ('Physics-IQ · I2V', 'Verified (%)', 'Verified'),
              'rbench': ('RBench Common Manipulation · I2V', 'Official normalized score (0–1)', 'Score'),
              'phygenbench': ('PhyGenBench · T2V', 'Overall · GPT-6.1-Sol MCQ', 'Overall'),
              'pcve': ('PCVE-RigidBench · V2V', 'PES', 'PES')}
LABELS = {'gpt61-sol': 'GPT-6.1-Sol', 'gpt62-sol': 'GPT-6.2-Sol',
          'gpt62-astra-native': 'GPT-6.2-Astra', 'gpt56-sol': 'GPT-5.6-Sol',
          'gpt56-terra': 'GPT-5.6-Terra', 'gpt56-luna': 'GPT-5.6-Luna',
          'deepseek-v41': 'DeepSeek-V4.1-Flash'}
COLORS = dict(zip(LABELS, ['#2563eb', '#dc2626', '#9333ea', '#0891b2', '#7c3aed', '#059669', '#d97706']))
MAIN = {'main720p24fps', 'standard720p24fps', 't2v720p24fps'}


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def collect(costs, main):
    rows, excluded = [], []
    for source in costs.get('rows', []):
        row = dict(source)
        bench, model = row.get('benchmark'), row.get('model')
        if bench not in BENCHMARKS or model not in LABELS or 'harness' in row.get('condition', ''):
            continue
        is_main = row.get('condition') in MAIN
        current = main.get('models', {}).get(model, {}).get(bench, {})
        # Main scores come from the current physical protocol, not the retired
        # PhyGen GPT-5.5 scores in the historical cost snapshot.
        score = current.get('score') if is_main else (row.get('metrics') or {}).get(BENCHMARKS[bench][2])
        reason = None
        if row.get('complete') != row.get('expected'):
            reason = 'generation incomplete'
        elif is_main and (current.get('counts', {}).get('complete') != row.get('expected') or
                          current.get('scored') != row.get('expected')):
            reason = 'current full-scope physical score pending'
        elif bench == 'phygenbench' and not is_main:
            reason = 'matched GPT-6.1-Sol analysis aggregate pending; GPT-5.5 excluded'
        elif not isinstance(score, (float, int)):
            reason = 'full-scope score pending'
        cost = row.get('standardized_cache70_cost_usd')
        if not isinstance(cost, (float, int)) or cost <= 0 or row.get('usage_missing_cases', 0):
            reason = reason or 'complete token accounting unavailable'
        if reason:
            excluded.append({'benchmark': bench, 'model': model, 'condition': row.get('condition'), 'reason': reason})
            continue
        row.update(score=score, cost_usd=cost, is_main=is_main,
                   score_source='current_main_suite' if is_main else 'verified_analysis_cost_snapshot')
        rows.append(row)
    return rows, excluded


def setting(row):
    value = row['condition']
    for suffix, title in [('res480', '480p'), ('res1080', '1080p'), ('fps12', '12 FPS'),
                          ('fps48', '48 FPS'), ('thinking_low', 'Low'), ('thinking_higher', 'Higher'), ('text', 'Text')]:
        if value.endswith(suffix):
            return title
    return 'Main'


def draw(bench, rows, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'svg.fonttype': 'none', 'pdf.fonttype': 42})
    fig, ax = plt.subplots(figsize=(8.0, 5.5))
    title, ylabel, _ = BENCHMARKS[bench]
    ax.set_title(title.replace('·', '/'), loc='left', weight='bold', fontsize=14)
    ax.set_xlabel('Estimated API cost / accepted video (USD; log scale)')
    ax.set_ylabel(ylabel)
    ax.set_xscale('log')
    ax.grid(axis='y', color='#e8edf4'); ax.set_axisbelow(True)
    ax.spines[['top', 'right']].set_visible(False)
    by_model = {}
    for row in rows:
        by_model.setdefault(row['model'], []).append(row)
    markers = {'Main': 'o', 'Text': 'x', '480p': 'v', '1080p': '^',
               '12 FPS': '<', '48 FPS': '>', 'Low': 's', 'Higher': 'D'}
    for model, values in by_model.items():
        main = next((r for r in values if r['is_main']), None)
        for group in [('480p', 'Main', '1080p'), ('12 FPS', 'Main', '48 FPS'), ('Low', 'Main', 'Higher')]:
            series = [next((r for r in values if setting(r) == name), None) for name in group]
            series = [r for r in series if r]
            if main and len(series) > 1:
                ax.plot([r['cost_usd'] for r in series], [r['score'] for r in series],
                        color=COLORS[model], alpha=.35, lw=1, linestyle='--' if 'Low' in group else '-')
        for row in values:
            name = setting(row)
            volume = {'480p': 4/9, '1080p': 2.25, '12 FPS': .5, '48 FPS': 2}.get(name, 1)
            ax.scatter(row['cost_usd'], row['score'], s=85 * volume, marker=markers[name],
                       color=COLORS[model], linewidths=1.2, zorder=3, alpha=.9)
    if not rows:
        ax.set_xlim(.01, 3); ax.set_ylim(0, 1)
        ax.text(.5, .5, 'Awaiting full-scope score and cost accounting', transform=ax.transAxes,
                ha='center', color='#64748b', wrap=True)
    else:
        ax.margins(x=.2, y=.18)
    legend = [Line2D([], [], marker='o', lw=0, color=COLORS[k], label=LABELS[k]) for k in LABELS if k in by_model]
    if legend:
        ax.legend(handles=legend, loc='best', fontsize=7, frameon=False)
    present = {setting(r) for r in rows}
    setting_legend = [Line2D([], [], marker=marker, lw=0, color='#64748b', label=name)
                      for name, marker in markers.items() if name in present]
    if setting_legend:
        fig.legend(handles=setting_legend, loc='lower center', bbox_to_anchor=(.5, .105),
                   ncol=4, frameon=False, fontsize=7)
    fig.text(.04, .055, 'Measured full-cohort scores; documented model tariffs, 70% cache assumed. No invented points.', fontsize=7, color='#64748b')
    fig.text(.04, .03, 'Bubble area: output pixel volume. Lines: one-factor changes. Retry, tool/GPU, and judge costs excluded.', fontsize=7, color='#64748b')
    fig.tight_layout(rect=(0, .19, 1, 1))
    for ext in ('svg', 'png', 'pdf'):
        fig.savefig(output.with_suffix('.' + ext), dpi=180, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def build(web, main_path, output=None):
    costs_path = web / 'cost_effect_progress.json'
    costs, main = read(costs_path), read(main_path)
    rows, excluded = collect(costs, main)
    output = output or web / 'cost_score_charts'
    output.mkdir(parents=True, exist_ok=True)
    for bench in BENCHMARKS:
        draw(bench, [r for r in rows if r['benchmark'] == bench], output / f'{bench}_scale')
    public_rows = [{k: r.get(k) for k in ('benchmark', 'condition', 'model', 'track', 'score', 'cost_usd',
                    'complete', 'expected', 'reported_cache_hit_rate', 'price_source', 'cost_audit', 'score_source')} for r in rows]
    proof = {'updated_utc': utc_now(), 'main_snapshot_utc': main.get('updated_utc'),
             'cost_snapshot_epoch': costs.get('updated_at'), 'cache_assumption': .7,
             'cost_snapshot_sha256': hashlib.sha256(costs_path.read_bytes()).hexdigest(),
             'main_snapshot_sha256': hashlib.sha256(main_path.read_bytes()).hexdigest(),
             'rows': public_rows, 'excluded': excluded, 'baseline_cost_score_pairs_verified': False}
    atomic_json(output / 'plot_data.json', proof)
    panels = ''.join(f'<figure><img src="cost_score_charts/{b}_scale.svg" alt="{html.escape(meta[0])}"><figcaption>{html.escape(meta[0])}</figcaption></figure>' for b, meta in BENCHMARKS.items())
    body = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="60"><title>PixelWorld · 成本与物理分</title><style>'
            'body{font:15px/1.6 system-ui;color:#182333;background:#f5f7fa;margin:0}main{max-width:1500px;margin:auto;padding:28px}'
            '.plots{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}figure{background:white;margin:0;border:1px solid #dfe5ee;border-radius:12px;padding:12px}img{width:100%}a{color:#2563eb}@media(max-width:850px){.plots{grid-template-columns:1fr}}</style>'
            '<main><a href="index.html">← 主实验</a> · <a href="analyses.html">分析进度</a><h1>成本与物理效果</h1>'
            '<p>横轴：每个有效视频的 API 成本估计；纵轴：benchmark 分数。颜色表示模型，形状表示设置，点面积表示输出像素量。'
            '使用实际全量评分，缓存统一按 70% 估计。失败重试、工具/GPU及评测费用另计；未评完或用量不齐的条件不补点。</p>'
            f'<p>当前可绘制 {len(rows)} 个实测设置；{len(excluded)} 个设置待完整评测或用量核验。PhyGenBench 只使用当前 GPT-6.1-Sol 适配，旧 GPT-5.5 分不混入。'
            'Video baseline 的论文分数保留在主表；未核实其同批生成设置与费用对应前，不把服务价与本地 H3 分数拼成成本点。</p>'
            '<div class="plots">' + panels + '</div><p><a href="cost_score_charts/plot_data.json">数据与排除原因</a></p></main></html>')
    table_rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
        [r['benchmark'], LABELS[r['model']], r['condition'], f"{r['score']:.4f}",
         f"${r['cost_usd']:.4f}", f"{r['complete']}/{r['expected']}"]) + '</tr>' for r in rows)
    details = ('<h2>实测设置明细</h2><div style="overflow:auto"><table style="width:100%;border-collapse:collapse">'
               '<thead><tr><th>Benchmark</th><th>Model</th><th>Setting</th><th>Score</th><th>Cost/video</th><th>Coverage</th></tr></thead>'
               '<tbody>' + table_rows + '</tbody></table></div>')
    body = body.replace('</main>', details + '</main>')
    (web / 'cost_score_plots.html').write_text(body)
    (web / 'cost_effects.html').write_text(body)
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-root', required=True, type=Path)
    parser.add_argument('--main-status', required=True, type=Path)
    args = parser.parse_args()
    result = build(args.web_root, args.main_status)
    print(json.dumps({'plotted': len(result['rows']), 'excluded': len(result['excluded'])}))
