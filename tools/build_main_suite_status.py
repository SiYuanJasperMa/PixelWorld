"""Build a public-safe progress snapshot for the revised four-benchmark suite."""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
import math
from pathlib import Path

from pixelworld.config import load_config
from pixelworld.supervisor import inspect
from pixelworld.util import atomic_json, utc_now
from tools.watch_rbench_common_scores import configs as rbench_configs

BENCHMARKS = {
    'physics_iq': ('physics_iq_720p24fps', 198, 'Verified'),
    'rbench': ('rbench_common_manipulation', 50, 'GPT branch'),
    'phygenbench': ('phygenbench_t2v_main720p24fps', 160, 'Overall'),
    'pcve': ('pcve_standard720p24fps', 129, 'PES'),
}
ROSTER = ('gpt62-sol', 'gpt61-sol', 'gpt6-astra', 'gpt56-sol', 'gpt56-terra',
          'gpt56-luna', 'deepseek-v41', 'seed21-pro')
COUNT_FIELDS = ('complete', 'running', 'pending', 'exhausted')
JUDGE = 'traex/GPT-6.1-Sol'
TRACK = 'vision-agent'


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def finite_score(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def count_config(config, model, expected):
    if len(config.case_ids) != expected or len(set(config.case_ids)) != expected:
        raise ValueError(f'Configured scope does not contain exactly {expected} unique cases')
    states = [inspect(config, model, TRACK, case) for case in config.case_ids]
    counts = Counter(row['state'] for row in states)
    latest = max((row.get('completed_utc') for row in states if row.get('completed_utc')), default=None)
    return {'counts': {key: counts[key] for key in COUNT_FIELDS}, 'last_delivery_utc': latest}


def public_counts(group, expected):
    values = {key: group.get('counts', {}).get(key, 0) for key in COUNT_FIELDS}
    if any(type(value) is not int or value < 0 for value in values.values()) or sum(values.values()) > expected:
        raise ValueError('Invalid published case counters')
    recent = group.get('latest_completion') or []
    return {'counts': values, 'last_delivery_utc': recent[0] if recent else None}


def legacy_score(record, expected, metric):
    score = record.get('score') or {}
    if record.get('full_scope_scored') is not True or score.get('scope') != expected:
        return None
    return finite_score(score.get('metrics', {}).get(metric))


def phygen_score(report, model):
    row = report.get('models', {}).get(model, {})
    if (report.get('version'), report.get('judge'), report.get('full160')) != (
        'phygen-gpt61-sol-three-stage-mcq-v1', JUDGE, True,
    ):
        return 0, None
    if report.get('cases') != list(range(1, 161)):
        return 0, None
    scored = row.get('cases', 0)
    return scored, finite_score(row.get('mean')) if scored == 160 else None


def rbench_score(report, model):
    if (report.get('protocol'), report.get('judge')) != ('rbench-common-official-gpt-branch-gpt61-sol-v1', JUDGE):
        return 0, None
    row = report.get('models', {}).get(model, {})
    scored = row.get('scored', 0)
    full = (row.get('full_common50') is True and scored == row.get('expected') == 50
            and not any(row.get(field) for field in ('waiting', 'generation_failures', 'judge_errors')))
    return scored, finite_score(row.get('mean')) if full else None


def appearance_scores(report):
    from tools.evaluate_video_photographic_gpt61 import RUBRIC_SHA256, VERSION

    if (report.get('phase'),report.get('judge'),report.get('version'),report.get('rubric_sha256')) != (
            'finished',JUDGE,VERSION,RUBRIC_SHA256) or report.get('failures'):
        return {}
    if report.get('completed_calls') != report.get('expected_calls'):
        return {}
    scope = {value[0]:(key,value[1]) for key,value in BENCHMARKS.items()}
    models = set(ROSTER) | {'seedance25-v1','minimax-h3-local'}
    results = {}
    for row in report.get('cohorts',[]):
        if row['benchmark'] not in scope or row['model'] not in models:
            continue
        key, expected = scope[row['benchmark']]
        cases, ratings, unavailable = (row[k] for k in ('cases','ratings','no_evidence'))
        assert type(cases) is int and 1 <= cases <= expected and ratings+unavailable == 2*cases
        complete = cases == expected and ratings == 2*expected
        score = finite_score(row.get('mean')) if complete else None
        if score is not None and not 1 <= score <= 5:
            raise ValueError('Appearance mean is outside the frozen scale')
        entry = results.setdefault(row['model'],{})
        if key in entry:
            raise ValueError('Duplicate appearance cohort')
        entry[key] = {'score':score,'cases':cases,'expected':expected,'ratings':ratings,
                      'no_evidence':unavailable,'complete':complete}
    return results


def harness_progress(root):
    """Project only model labels, case counts, and launch state from private runs."""
    joined = {}
    registry = read(root/'dispatch.json').get('models', {})
    for model in ROSTER:
        if model not in registry:
            continue
        matrix = {}
        for harness in ('opencode','claude_code'):
            entry = registry[model].get(harness)
            if not entry:
                continue
            expected = entry['registered_cases']
            if expected != BENCHMARKS['physics_iq'][1]:
                raise ValueError('Unexpected local native matrix scope')
            config_path = Path(entry['config']).resolve()
            if not config_path.is_relative_to(root.resolve()):
                raise ValueError('Unregistered native matrix configuration')
            config = read(config_path)
            runs = Path(config['runs_root']).resolve()
            if not runs.is_relative_to(root.resolve()):
                raise ValueError('Unregistered native matrix output directory')
            released = entry.get('full_supervisor_started') is True
            state = 'registered'
            counts = public_counts({}, expected)['counts']
            if released:
                snap = read(runs/'status.json')
                group = snap.get('groups', {}).get(f'{model}/{TRACK}')
                if group is not None and snap.get('cases') == expected and group.get('model_key') == model:
                    counts = public_counts(group, expected)['counts']
                    state = 'full_complete' if counts['complete'] == expected else 'full_released'
                else:
                    state = 'full_released'
            elif entry.get('acceptance_pid'):
                case = entry['canary_case']
                if not isinstance(case, str) or not case.isascii() or not case.isdecimal():
                    raise ValueError('Invalid native canary case identity')
                attempt = read(runs/model/TRACK/f'case_{case}_attempt_01/run_manifest.json')
                try:
                    cmdline = (Path('/proc')/str(int(entry['acceptance_pid']))/'cmdline').read_bytes()
                except OSError:
                    cmdline = b''
                active = str(config_path).encode() in cmdline.split(b'\0')
                state = {'failed':'canary_failed','complete':'canary_scoring'}.get(
                    attempt.get('status'),'canary_running' if active else 'canary_waiting')
            matrix[harness] = {'state':state,'counts':counts,'expected':expected,'full_released':released}
        joined[model] = matrix
    return joined


def build(nightly, publication):
    cat = read(publication / 'catalogue.json')
    ev = read(publication / 'evaluation.json')
    cohorts = {(x.get('suite'), x.get('model_key')): x for x in ev.get('cohorts', []) if x.get('track') == TRACK}
    groups = {(x.get('suite'), x.get('model_key')): x for x in cat.get('groups', []) if x.get('track') == TRACK}
    evaluation = nightly / 'evaluation'
    rbench = read(evaluation / 'rbench_common_gpt61_live/execution.json')
    phygen = read(evaluation / 'phygen_gpt61_sol_three_stage/summary.json')
    phygen_deepseek = read(evaluation / 'phygen_gpt61_sol_deepseek/summary.json')
    photo = read(evaluation / 'photographic_gpt61_existing_main/execution.json')
    fvd_report = read(evaluation / 'physics_iq_fvd_official_take1/summary.json')
    pcve_fvd = {row['model']: row for row in read(evaluation / 'pcve_reused_official_fvd.json').get('verified_scores', [])}
    rconfigs = rbench_configs(nightly / 'rbench_full_dispatch')
    registry = read(nightly / 'gpt62_cohorts/dispatch_registry.json')['configured']
    gpt62 = {x['name']: load_config(Path(x['config'])) for x in registry if x['name'].endswith('_full')}
    rows = {}
    for model in ROSTER:
        cells = {}
        for key, (suite, expected, metric) in BENCHMARKS.items():
            if key == 'rbench':
                progress = count_config(*rconfigs[model], expected) if model in rconfigs else public_counts({}, expected)
                scored, score = rbench_score(rbench, model)
            else:
                config = gpt62.get(key + '_full') if model == 'gpt62-sol' else None
                progress = count_config(config, config.model(model), expected) if config else public_counts(groups.get((suite, model), {}), expected)
                record = cohorts.get((suite, model), {})
                if key == 'phygenbench':
                    scored, score = phygen_score(phygen, model)
                    if model == 'deepseek-v41' and not scored:
                        scored, score = phygen_score(phygen_deepseek, model)
                else:
                    score = legacy_score(record, expected, metric)
                    scored = expected if score is not None else 0
            if progress['counts']['complete'] < expected:
                score = None
            cells[key] = {**progress, 'expected': expected, 'scored': scored, 'score': score, 'metric': metric}
            if key == 'physics_iq' and fvd_report.get('full198') is True:
                detail = fvd_report.get('scores', {}).get(model, {})
                if detail.get('count') == expected:
                    cells[key]['fvd'] = finite_score(detail.get('fvd'))
            elif key == 'pcve' and score is not None:
                detail = pcve_fvd.get(model, {})
                reference = cohorts.get((suite, model), {}).get('score') or {}
                fvd = finite_score(reference.get('metrics', {}).get('FVD'))
                if detail.get('count') == expected and fvd is not None and detail.get('fvd') == fvd:
                    cells[key]['fvd'] = fvd
        rows[model] = cells
    plan = read(publication / 'experiment_plan.json')
    baseline_keys = {'seedance25': 'seedance25-v1', 'minimax-h3': 'minimax-h3-local'}
    references = {}
    for row in plan.get('video_baselines', []):
        key = row.get('key')
        if key not in baseline_keys:
            continue
        scores = {}
        for benchmark in ('physics_iq', 'pcve'):
            item = row.get('scores', {}).get(benchmark, {})
            if item.get('kind') == 'published_reference':
                scores[benchmark] = finite_score(item.get('value'))
        _, scores['phygenbench'] = phygen_score(phygen, baseline_keys[key])
        references[key] = scores
    return {'protocol': 'pixelworld-main-suite-20261124', 'updated_utc': utc_now(), 'models': rows,
            'references': references,
            'harness_progress': harness_progress(nightly/'controlled_harness_matrix'),
            'photographic_progress': {key: photo.get(key) for key in ('completed_calls', 'expected_calls')},
            'photographic_scores': appearance_scores(photo),
            'photographic_judge': JUDGE}


LABELS = {
    'gpt62-sol': 'GPT-6.2-Sol', 'gpt61-sol': 'GPT-6.1-Sol', 'gpt6-astra': 'GPT-6-Astra',
    'gpt56-sol': 'GPT-5.6-Sol', 'gpt56-terra': 'GPT-5.6-Terra', 'gpt56-luna': 'GPT-5.6-Luna',
    'deepseek-v41': 'DeepSeek-V4.1-Flash', 'seed21-pro': 'Seed-2.1-Pro-0915',
}
HEADER = {
    'physics_iq': ('Physics-IQ Verified', 'I2V · 198 cases', 'physics_iq_baseline.html'),
    'rbench': ('RBench Common Manipulation', 'I2V · 50 cases', ''),
    'phygenbench': ('PhyGenBench', 'T2V · 160 cases', 'phygen_pixelspace.html'),
    'pcve': ('PCVE-RigidBench', 'V2V editing · 129 cases', 'pcve_rigid.html'),
}


def render(status):
    def display_score(key, score):
        digits = {'physics_iq': 2, 'pcve': 3, 'rbench': 3, 'phygenbench': 4}[key]
        return f'{score:.{digits}f}'

    def cell(row, key, model):
        count, total = row['counts'], row['expected']
        _, _, href = HEADER[key]
        progress = f"<b>{count['complete']} / {total}</b>"
        if href and model != 'gpt62-sol':
            progress = f'<a href="{href}?model={model}">{progress}</a>'
        if row['score'] is None:
            evaluation = f"评分 {row['scored']} / {total}"
        else:
            evaluation = f"<b>{escape(row['metric'])} {display_score(key, row['score'])}</b>"
        recent = row.get('last_delivery_utc')
        last = f'<time datetime="{escape(recent)}">{escape(recent)}</time>' if recent else '尚无交付'
        return (f'<td>{progress}<div class="progress"><i style="width:{100*count["complete"]/total:.1f}%"></i></div>'
                f'<div class="small muted">{count["running"]} 运行 · {count["pending"]} 待生成 · {count["exhausted"]} 终止</div>'
                f'<div>{evaluation}</div><div class="small muted">最近交付：{last}</div></td>')

    def baseline_cell(scores, key):
        value = scores.get(key)
        if value is None:
            return '<td>—</td>'
        origin = 'GPT-6.1-Sol 重评' if key == 'phygenbench' else '论文 / 榜单参考'
        return f'<td><b>{display_score(key, value)}</b><br><span class="small muted">{origin}</span></td>'

    heads = ''.join(f'<th>{label}<br><span class="small muted">{spec}</span></th>' for label, spec, _ in HEADER.values())
    baseline_labels = {'seedance25': 'Seedance 2.5', 'minimax-h3': 'MiniMax H3'}
    rows = ''.join(f'<tr class="baseline"><th>{baseline_labels[model]}<br><span class="small muted">VideoGen baseline</span></th>' +
                   ''.join(baseline_cell(scores, k) for k in HEADER) + '</tr>'
                   for model, scores in status.get('references', {}).items())
    rows += ''.join(f'<tr><th>{LABELS[model]}</th>' + ''.join(cell(cells[k], k, model) for k in HEADER) + '</tr>'
                    for model, cells in status['models'].items())
    fvd_rows = ''.join(f'<tr><th>{LABELS[model]}</th>' + ''.join(
        '<td>—</td>' if cells[k].get('fvd') is None else f'<td>{cells[k]["fvd"]:.2f}</td>'
        for k in ('physics_iq', 'pcve')) + '</tr>' for model, cells in status['models'].items()
        if any(cells[k].get('fvd') is not None for k in ('physics_iq', 'pcve')))
    def harness_cell(entry):
        if not entry:
            return '<td>—</td>'
        counts = entry['counts']
        if entry['full_released']:
            return (f"<td><b>{counts['complete']} / {entry['expected']}</b><br><span class='small muted'>"
                    f"{counts['running']} 运行 · {counts['pending']} 待生成 · {counts['exhausted']} 终止</span></td>")
        phase = {'canary_running':'验收生成中','canary_scoring':'验收评分 / 归档','canary_failed':'验收未通过',
                 'canary_waiting':'验收待核查','registered':'已登记'}[entry['state']]
        return f'<td>{phase}<br><span class="small muted">未发全量</span></td>'

    harness = status.get('harness_progress', {})
    harness_rows = ''.join(f"<tr><th>{LABELS[model]}</th><td><b>{status['models'][model]['physics_iq']['counts']['complete']} / 198</b></td>" +
                 ''.join(harness_cell(entries.get(k)) for k in ('opencode','claude_code'))+'</tr>'
                 for model,entries in harness.items())
    harness_section = (f'<details id="harness-progress"><summary>Physics-IQ · Harness 对照进度</summary>'
        '<p class="small muted">Vision · 同模型与输入 · 720p / 24 FPS。只显示有效交付，验收样本不是全量分数。</p>'
        '<div class="table"><table><thead><tr><th>模型</th><th>Codex 基准</th><th>OpenCode</th><th>Claude Code</th>'
        f'</tr></thead><tbody>{harness_rows}</tbody></table></div></details>' if harness_rows else '')
    def photo_cell(row):
        if row is None:
            return '<td>—</td>'
        score = row['score']
        value = '—' if score is None else f'<b>{score:.2f}</b>'
        label = f"{row['cases']} / {row['expected']} 题"
        if row['no_evidence']:
            label += f" · {row['no_evidence']} 次无法评分"
        return f'<td>{value}<br><span class="small muted">{label}</span></td>'

    labels = {**LABELS,'seedance25-v1':'Seedance 2.5','minimax-h3-local':'MiniMax H3 · 本地'}
    photos = status.get('photographic_scores', {})
    photo_rows = ''.join(f'<tr><th>{labels[model]}</th>' + ''.join(photo_cell(cells.get(k)) for k in HEADER) + '</tr>'
                         for model,cells in photos.items())
    photo_table = ('<h3>主观画质（1–5，越高越好）</h3><div class="table"><table><thead><tr><th>模型</th>' + heads +
                   f'</tr></thead><tbody>{photo_rows}</tbody></table></div>'
                   '<p class="small muted">仅在整个 benchmark 的每题两种播放顺序均可评分时显示均值。'
                   'RBench 当前只有 1 题验收；各 benchmark 内容和条件不同，不据此推断输入条件的因果效应。</p>'
                   if photo_rows else '')
    photo = status['photographic_progress']
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pixelworld-updated" content="{status['updated_utc']}"><meta name="pixelworld-protocol" content="pixelworld-main-suite-20261124">
<title>PixelWorld · 主实验</title><link rel="stylesheet" href="formal.css"><body><header><nav><strong><a href="index.html">PixelWorld</a></strong>
<span><a href="analyses.html">分析进度</a><a href="cost_effects.html">成本与效果</a><a href="pixelworld-index.html">历史总览</a>
<a href="robojudge.html">RoboJudge</a><a href="https://huggingface.co/datasets/HuggingFriends/PixelWorld">视频与 Trace</a>
<a href="https://github.com/SiyuanMaCS/PixelWorld/tree/collaboration/20261123-harness-evaluation-sync">GitHub</a></span></nav></header>
<main><p class="eyebrow">CODING AGENTS AS PIXEL WORLD MODELS</p><h1>主实验</h1>
<p class="muted">Vision · 720p / 24 FPS · 数据快照 <time datetime="{escape(status['updated_utc'])}">{escape(status['updated_utc'])}</time></p>
<div class="table"><table><thead><tr><th>模型</th>{heads}</tr></thead><tbody>{rows}</tbody></table></div>
<p class="small muted">只有评完全部样本才显示总分。RBench 只生成一个候选，使用 GPT-6.1-Sol 的官方 GPT 分支适配，不是 GPT + Qwen 联合榜分。
PhyGenBench 为 GPT-6.1-Sol 三阶段 MCQ 适配，不与原评测器分数混排。PCVE 采用 Full 129；PES 校准与论文参照见详情页。</p>
{harness_section}
<h2>视频质量 · 独立于物理分</h2><p>GPT-6.1-Sol 画质判分进度：{photo.get('completed_calls') or 0} / {photo.get('expected_calls') or 0} 次调用。</p>
{photo_table}
<h3>FVD（越低越好）</h3>
<div class="table"><table><thead><tr><th>模型</th><th>Physics-IQ FVD ↓</th><th>PCVE FVD ↓</th></tr></thead><tbody>{fvd_rows}</tbody></table></div>
<p class="small muted">FVD 使用各 benchmark 对应的参考集，不能跨 benchmark 比较。</p></main>
<script>
const clock = new Intl.RelativeTimeFormat('zh-CN', {{numeric: 'always'}});
function refreshAges() {{
  for (const time of document.querySelectorAll('time[datetime]')) {{
    const ms = Date.parse(time.dateTime);
    if (!Number.isFinite(ms)) continue;
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    const [divisor, unit] = sec < 60 ? [1, 'second'] : sec < 3600 ? [60, 'minute'] :
      sec < 86400 ? [3600, 'hour'] : [86400, 'day'];
    time.title = time.dateTime;
    time.textContent = clock.format(-Math.floor(sec / divisor), unit);
  }}
}}
refreshAges();
setInterval(refreshAges, 15000);
const published = document.querySelector('meta[name="pixelworld-updated"]').content;
let reloading = false;
async function checkSnapshot() {{
  if (reloading) return;
  try {{
    const reply = await fetch('main_suite_status_v2.json?_=' + Date.now(), {{cache: 'no-store'}});
    if (!reply.ok) return;
    const data = await reply.json();
    if (data.updated_utc && data.updated_utc !== published) {{
      reloading = true;
      location.replace(location.pathname + '?v=' + encodeURIComponent(data.updated_utc));
    }}
  }} catch (error) {{}}
}}
checkSnapshot();
setInterval(checkSnapshot, 60000);
</script></body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nightly', type=Path, required=True)
    p.add_argument('--publication', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--html', type=Path, help='Optional standalone preview next to formal.css')
    a = p.parse_args()
    status = build(a.nightly, a.publication)
    atomic_json(a.output, status)
    if a.html:
        a.html.write_text(render(status), encoding='utf-8')


if __name__ == '__main__':
    main()
