#!/usr/bin/env python3
"""Build the live analysis-experiment progress page from matrix status files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs/final_release_candidate_20261117"
MODEL_LABELS = {
    "gpt61-sol": "GPT-6.1-Sol",
    "gpt56-sol": "GPT-5.6-Sol",
    "gpt56-terra": "GPT-5.6-Terra",
    "gpt56-luna": "GPT-5.6-Luna",
    "deepseek-v41": "DeepSeek-V4.1-Flash",
}
CONDITIONS = [
    {"id": "physics_iq_text", "title": "Physics-IQ · Text", "question": "Text vs Vision", "track": "text", "cases": 198, "runs": "physics_iq_text", "config": "physics_iq_text_analysis.json"},
    {"id": "physics_iq_res480", "title": "Physics-IQ · 480p", "question": "分辨率下界", "track": "vision-agent", "cases": 198, "runs": "physics_iq_res480", "config": "physics_iq_res480_analysis.json"},
    {"id": "physics_iq_fps12", "title": "Physics-IQ · 12 FPS", "question": "低帧率", "track": "vision-agent", "cases": 198, "runs": "physics_iq_fps12", "config": "physics_iq_fps12_analysis.json"},
    {"id": "physics_iq_fps48", "title": "Physics-IQ · 48 FPS", "question": "高帧率", "track": "vision-agent", "cases": 198, "runs": "physics_iq_fps48", "config": "physics_iq_fps48_analysis.json"},
    {"id": "physics_iq_res1080", "title": "Physics-IQ · 1080p", "question": "分辨率上界", "track": "vision-agent", "cases": 198, "runs": "physics_iq_res1080", "config": "physics_iq_res1080_analysis.json"},
    {"id": "physics_iq_thinking_low", "title": "Physics-IQ · Thinking low", "question": "推理预算下界；High 复用主实验", "track": "vision-agent", "cases": 198, "runs": "physics_iq_thinking_low", "config": "physics_iq_thinking_low_analysis.json"},
    {"id": "physics_iq_thinking_higher", "title": "Physics-IQ · Thinking xhigh / max", "question": "GPT 请求 xhigh；DeepSeek 请求 max；API 接受已实测，内部推理深度由 provider 控制", "track": "vision-agent", "cases": 198, "runs": "physics_iq_thinking_higher", "config": "physics_iq_thinking_higher_analysis.json"},
    {"id": "robojudge_text", "title": "RoboJudge · Text", "question": "Text vs Vision", "track": "text", "cases": 50, "runs": "robojudge_text", "config": "robojudge_text_analysis.json"},
    {"id": "pcve_text", "title": "PCVE · Text", "question": "Text vs Vision", "track": "text", "cases": 129, "runs": "pcve_text", "config": "pcve_text_analysis.json"},
    {"id": "rbench_text", "title": "RBench Common Manipulation · Text", "question": "Text vs Vision；Vision复用主实验", "track": "text", "cases": 50, "runs": "rbench_text", "config": "rbench_text_analysis.json"},
    {"id": "phygenbench_text", "title": "PhyGenBench · Text", "question": "Text vs Vision", "track": "text", "cases": 160, "runs": "phygenbench_text", "config": "phygenbench_text_analysis.json"},
    {"id": "physics_iq_harness_pi", "title": "Physics-IQ · Pi 1.6.0", "question": "同模型换 agent harness · 720p24fps · 原生工具与内核隔离", "track": "vision-agent", "cases": 198, "runs": "physics_iq_harness_pi", "config": "physics_iq_harness_pi_analysis.json"},
]
PLANNED = []


def read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def normalized(counts: dict, cases: int) -> dict:
    return {key: int(counts.get(key, 0) or 0) for key in ("complete", "running", "pending", "exhausted")}


def build(web_root: Path, run_root: Path = RUN_ROOT) -> dict:
    if not run_root.is_dir():raise FileNotFoundError('Analysis run bundle does not exist')
    metadata=read(run_root/'bundle.json')
    runtime=read(run_root/'analysis_runtime_sources.json')
    runtime_rows=runtime.get('sources',[])
    model_labels={m['key']:m.get('display_name',m['model'].split('/')[-1]) for m in metadata.get('models',[]) if m.get('analysis')}
    if not metadata:model_labels=MODEL_LABELS
    model_labels.update(runtime.get('model_labels',{}))
    order = list(MODEL_LABELS)
    model_labels = dict(sorted(model_labels.items(), key=lambda row:
                              order.index(row[0]) if row[0] in order else len(order)))
    limits = read(run_root / "configs/scheduling_limits.json")
    paused = limits.get("paused_models_by_config", {})
    evaluation = read(web_root / 'evaluation.json')
    evaluation_rows = {(row.get('suite'), row.get('model_key'), row.get('track')): row
                       for row in evaluation.get('cohorts', [])}
    harness_plan = read(run_root / 'analysis_plans/harness_comparison.json')
    archived_harness_ids = set(harness_plan.get('archived_condition_ids', []))
    archived_harness_ids.update(runtime.get('retired_conditions',[]))
    conditions = []
    totals = {"complete": 0, "running": 0, "pending": 0, "exhausted": 0, "total": 0}
    for spec in CONDITIONS:
        if spec['id'] in archived_harness_ids or not (run_root/'configs'/spec['config']).is_file():continue
        status = read(run_root / "analysis_generations" / spec["runs"] / "status.json")
        config=read(run_root/'configs'/spec['config'])
        first=(config.get('case_ids')or[None])[0]
        media=read(Path(config.get('inputs_root',''))/str(first)/'task_spec.json') if first else {}
        output_text=(f"{media.get('width')}×{media.get('height')} · {media.get('fps')} FPS · {media.get('frames')} frames"
                     if media else '')
        bench=next(name for name in ('physics_iq','robojudge','pcve','phygenbench','rbench') if spec['id'].startswith(name))
        page={'physics_iq':'physics_iq_baseline.html','robojudge':'robojudge.html',
              'pcve':'pcve_rigid.html','phygenbench':'phygen_pixelspace.html','rbench':'rbench_common_manipulation.html'}[bench]
        groups = status.get("groups", {})
        source_updates=[status.get('updated_utc')]
        for source in runtime_rows:
            if source.get('condition')!=spec['id']:continue
            cfg=read(Path(source['config']));key=source['model_key']
            model=next((m for m in cfg.get('models',[]) if m.get('key')==key),None)
            ids=cfg.get('case_ids',[])
            if not model or model.get('tracks')!=[spec['track']] or len(ids)!=spec['cases'] or len(set(ids))!=spec['cases']:
                raise ValueError('Analysis runtime source has a different model, track or case scope')
            snapshot=read(Path(cfg['runs_root'])/'status.json')
            group=snapshot.get('groups',{}).get(f'{key}/{spec["track"]}')
            if group:
                counts=group.get('counts',{})
                if any(type(n) is not int or n<0 for n in counts.values()) or sum(counts.values())>spec['cases']:
                    raise ValueError('Invalid runtime analysis counts')
                groups[f'{key}/{spec["track"]}']=group
                source_updates.append(snapshot.get('updated_utc'))
        rows = []
        counts = {"complete": 0, "running": 0, "pending": 0, "exhausted": 0}
        for key in model_labels:
            group_key = f"{key}/{spec['track']}"
            group = groups.get(group_key, {})
            row_counts = normalized(group.get("counts", {}), spec["cases"])
            # A closed dispatch window is not a terminal model failure when a
            # documented infrastructure interruption leaves retries available.
            infra_pending = group.get("infrastructure_retry_pending", 0)
            if type(infra_pending) is int and 0 < infra_pending <= row_counts["exhausted"]:
                row_counts["exhausted"] -= infra_pending
                row_counts["pending"] += infra_pending
            row_counts["pending"] += max(0, spec["cases"] - sum(row_counts.values()))
            for name in counts:
                counts[name] += row_counts[name]
            assessed = evaluation_rows.get((spec['id'], key, spec['track']), {})
            full_score = bool(assessed.get('full_scope_scored') and row_counts['complete'] == spec['cases'])
            evaluation_phase = 'complete' if full_score else assessed.get('phase', 'waiting_for_generation')
            if not full_score and evaluation_phase == 'complete':
                evaluation_phase = 'blocked_current_generation_mismatch'
            rows.append({
                "key": key, "label": model_labels[key],
                **row_counts, "total": spec["cases"],
                "mean_duration_seconds": group.get("mean_duration_seconds"),
                "latest_completion": group.get("latest_completion"),
                "evaluation": {
                    "phase": evaluation_phase,
                    "full_scope_scored": full_score,
                    "metrics": (assessed.get('score') or {}).get('metrics') if full_score else None,
                    "progress": {k:v for k,v in (assessed.get('evaluation_progress') or {}).items()
                                 if k in ('completed','expected','updated_utc')},
                },
            })
        total = spec["cases"] * len(model_labels)
        conditions.append({
            **spec, "total": total, "counts": counts,
            "page":page,"output_text":output_text,
            "paused": spec["config"] in paused or status.get("dispatch_paused") is True,
            "updated_utc": max((x for x in source_updates if isinstance(x,str)),default=None),
            "models": rows,
            "scored_models": sum(row['evaluation']['full_scope_scored'] for row in rows),
            "primary_metric": {'physics_iq':'Verified','robojudge':'Mean','pcve':'PES','phygenbench':'Overall','rbench':'GPT branch'}[bench],
        })
        for name in totals:
            totals[name] += counts.get(name, 0)
        totals["total"] += total
    harness_plans = harness_plan.get('conditions', [])
    for spec in [*PLANNED, *harness_plans]:
        if spec.get('id') in archived_harness_ids or any(c['id']==spec.get('id') for c in conditions):
            continue
        if not spec.get('planned'):
            continue
        selected_models = spec.get('model_keys', list(model_labels))
        total = spec["cases"] * len([key for key in selected_models if key in model_labels])
        conditions.append({**spec, "total": total, "counts": {"complete": 0, "running": 0, "pending": 0, "exhausted": 0},
                           "paused": True, "models": []})
        # Planned conditions are listed separately from registered runnable cases.
    result = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "models": model_labels,
        "totals": totals,
        "conditions": conditions,
        "planned_total": sum(item["total"] for item in conditions if item.get("planned")),
    }
    (web_root / "analysis_progress.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (web_root / "analyses.html").write_text(PAGE)
    return result


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PixelWorld · 分析实验进度</title><link rel="stylesheet" href="formal.css">
<style>
body{background:#f5f7fa}.wrap{max-width:1320px;margin:auto;padding:26px}.head{display:flex;justify-content:space-between;gap:20px;align-items:end}.head h1{margin:4px 0 6px}.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:22px 0}.stat{background:#fff;border:1px solid #dfe5ee;border-radius:12px;padding:14px}.stat b{display:block;font-size:24px}.card{background:#fff;border:1px solid #dfe5ee;border-radius:13px;padding:18px;margin:14px 0}.bar{height:8px;background:#e8edf4;border-radius:8px;overflow:hidden;margin:10px 0}.bar i{display:block;height:100%;background:#3978dd}.row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.muted{color:#64748b}.small{font-size:12px}.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;background:#eaf0f7;color:#526479}.active{background:#e4f2ed;color:#19724e}.paused{background:#fff1d6;color:#97651a}.planned{background:#eef0f4;color:#667085}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #eef1f5;font-size:13px}th{color:#65758d;font-size:11px}.num{font-variant-numeric:tabular-nums}@media(max-width:800px){.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="head"><div><p class="small muted">ANALYSIS · LIVE PROGRESS</p><h1>分析实验进度</h1><p class="muted small">免费模型 · Text / 分辨率 / FPS / Thinking · 主实验 Vision 720p/24fps 复用</p></div><p class="small muted" id="sync">加载中…</p></div>
<div class="stats" id="stats"></div><div id="content"></div>
<p><a href="cost_effects.html">成本 / 效果</a> · <a href="phygen_agent_prompt_init.html">Legacy · 已停止的首帧扩展</a> · <a href="index.html">主实验总览</a> · <a href="phygen_acd_smoke.html">Legacy · 旧初始化实验</a></p>
<p class="small muted">分析使用 GPT-6.2-Sol / GPT-6.1-Sol / GPT-5.6-Sol / GPT-5.6-Terra / GPT-5.6-Luna / DeepSeek-V4.1-Flash，均为免费模型；进度按各矩阵 status.json 统计，暂停条件显示为 paused。</p>
</div><script>
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>typeof n==='number'?n.toLocaleString():'—',pct=(a,b)=>b?Math.min(100,100*a/b):0;
function ago(s){if(!s)return'—';const t=new Date(s).getTime();if(!isFinite(t))return'—';const d=Math.max(0,(Date.now()-t)/1000);if(d<90)return Math.round(d)+' 秒前';if(d<5400)return Math.round(d/60)+' 分钟前';return Math.round(d/3600)+' 小时前'}
function evalStatus(m,c){const e=m.evaluation||{},v=e.metrics?.[c.primary_metric];if(e.full_scope_scored&&typeof v==='number')return esc(c.primary_metric)+' '+v.toFixed(c.primary_metric==='PES'||c.primary_metric==='Overall'?3:2);const names={official_evaluation:'评分中',official_full_evaluation:'评分中',adapter_evaluation:'评分中',preparing_evaluation:'评测准备中',waiting_for_evaluation_retry:'评测待重试',stale_evaluation:'评分待恢复',incomplete_generation:'全量未齐',blocked_score_provenance:'分数待核验',blocked_current_generation_mismatch:'范围待核验'};const label=names[e.phase]||(m.complete===m.total?'待评分':'待生成完成'),p=e.progress||{};return typeof p.completed==='number'&&p.completed>0?'已评 '+fmt(p.completed)+' / '+fmt(p.expected)+' · '+label:label}
function render(d){$('sync').textContent='更新：'+new Date(d.updated_utc).toLocaleString();const t=d.totals;$('stats').innerHTML=[['有效交付',t.complete],['运行中',t.running],['待生成',t.pending],['待重试/核验',t.exhausted],['总 case',t.total]].map(([k,v])=>'<div class="stat"><b>'+fmt(v)+'</b><span class="small muted">'+k+'</span></div>').join('');
$('content').innerHTML=d.conditions.map(c=>{const p=pct(c.counts.complete,c.total),badge=c.planned?'<span class="badge planned">planned</span>':c.paused?'<span class="badge paused">paused</span>':(c.models||[]).length>0&&c.scored_models===(c.models||[]).length?'<span class="badge active">已评分</span>':c.counts.complete===c.total?'<span class="badge active">生成已齐</span>':!c.counts.pending&&!c.counts.running?'<span class="badge paused">等待检查并补跑</span>':'<span class="badge active">运行中</span>';const rows=(c.models||[]).map(m=>'<tr><td><a href="'+esc(c.page)+'?suite='+encodeURIComponent(c.id)+'&track='+encodeURIComponent(c.track)+'&model='+encodeURIComponent(m.key)+'">'+esc(m.label)+'</a></td><td class="num">'+fmt(m.complete)+' / '+fmt(m.total)+'</td><td class="num">'+fmt(m.running)+'</td><td class="num">'+fmt(m.pending)+'</td><td class="num">'+fmt(m.exhausted)+'</td><td class="small">'+evalStatus(m,c)+'</td><td class="num">'+(m.mean_duration_seconds?Math.round(m.mean_duration_seconds/60)+' 分钟':'—')+'</td><td class="small muted">'+ago(m.latest_completion?.[0])+'</td></tr>').join('');return '<div class="card"><div class="row"><div><b>'+esc(c.title)+'</b> '+badge+'<div class="small muted">'+esc(c.question)+' · '+esc(c.track)+' · '+fmt(c.cases)+' prompts/model</div></div><div class="small muted">更新：'+ago(c.updated_utc)+'</div></div><div class="bar"><i style="width:'+p+'%"></i></div><div class="row small"><span>完成 <b>'+fmt(c.counts.complete)+'</b> / '+fmt(c.total)+'</span><span>运行 '+fmt(c.counts.running)+' · 待生成 '+fmt(c.counts.pending)+' · 待核验 '+fmt(c.counts.exhausted)+'</span></div>'+(rows?'<details><summary class="small">按模型展开</summary><table><thead><tr><th>模型</th><th>完成</th><th>运行</th><th>待生成</th><th>待重试/核验</th><th>评测</th><th>平均耗时</th><th>最近交付</th></tr></thead><tbody>'+rows+'</tbody></table></details>':'')+'</div>'}).join('')}
async function poll(){try{const r=await fetch((document.body.dataset.liveBase||'')+'analysis_progress.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);render(await r.json())}catch(e){$('sync').textContent='同步等待：'+e.message}}
poll();setInterval(poll,30000);
</script></body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, default=RUN_ROOT / "live_preview/pixelworld")
    parser.add_argument("--bundle", type=Path, default=RUN_ROOT)
    args = parser.parse_args()
    result = build(args.web_root.resolve(),args.bundle.resolve())
    print(json.dumps({"conditions": len(result["conditions"]), "totals": result["totals"]}, ensure_ascii=False))
