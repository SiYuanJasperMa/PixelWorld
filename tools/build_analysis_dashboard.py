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
    {"id": "phygenbench_text", "title": "PhyGenBench · Text", "question": "Text vs Vision", "track": "text", "cases": 160, "runs": "phygenbench_text", "config": "phygenbench_text_analysis.json"},
]
PLANNED = [
    {"id": "phygen_i2v", "title": "PhyGenBench · AgentPixel → Qwen → I2V", "question": "agent 构建场景 + Qwen 真实化", "track": "vision-agent", "cases": 160, "planned": True},
]


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
    model_labels={m['key']:m.get('display_name',m['model'].split('/')[-1]) for m in metadata.get('models',[]) if m.get('analysis')}
    if not metadata:model_labels=MODEL_LABELS
    limits = read(run_root / "configs/scheduling_limits.json")
    paused = limits.get("paused_models_by_config", {})
    conditions = []
    totals = {"complete": 0, "running": 0, "pending": 0, "exhausted": 0, "total": 0}
    for spec in CONDITIONS:
        if not (run_root/'configs'/spec['config']).is_file():continue
        status = read(run_root / "analysis_generations" / spec["runs"] / "status.json")
        config=read(run_root/'configs'/spec['config'])
        first=(config.get('case_ids')or[None])[0]
        media=read(Path(config.get('inputs_root',''))/str(first)/'task_spec.json') if first else {}
        output_text=(f"{media.get('width')}×{media.get('height')} · {media.get('fps')} FPS · {media.get('frames')} frames"
                     if media else '')
        bench=next(name for name in ('physics_iq','robojudge','pcve','phygenbench') if spec['id'].startswith(name))
        page={'physics_iq':'physics_iq_baseline.html','robojudge':'robojudge.html',
              'pcve':'pcve_rigid.html','phygenbench':'phygen_pixelspace.html'}[bench]
        groups = status.get("groups", {})
        rows = []
        counts = {"complete": 0, "running": 0, "pending": 0, "exhausted": 0}
        for key in model_labels:
            group_key = f"{key}/{spec['track']}"
            group = groups.get(group_key, {})
            row_counts = normalized(group.get("counts", {}), spec["cases"])
            row_counts["pending"] += max(0, spec["cases"] - sum(row_counts.values()))
            for name in counts:
                counts[name] += row_counts[name]
            rows.append({
                "key": key, "label": model_labels[key],
                **row_counts, "total": spec["cases"],
                "mean_duration_seconds": group.get("mean_duration_seconds"),
                "latest_completion": group.get("latest_completion"),
            })
        total = spec["cases"] * len(model_labels)
        conditions.append({
            **spec, "total": total, "counts": counts,
            "page":page,"output_text":output_text,
            "paused": spec["config"] in paused,
            "updated_utc": status.get("updated_utc"),
            "models": rows,
        })
        for name in totals:
            totals[name] += counts.get(name, 0)
        totals["total"] += total
    for spec in PLANNED:
        total = spec["cases"] * len(model_labels)
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
<div class="head"><div><p class="small muted">ANALYSIS · LIVE PROGRESS</p><h1>分析实验进度</h1><p class="muted small">5 个模型 · Text / 分辨率 / FPS / 场景构造 · 主实验 Vision 720p/24fps 复用</p></div><p class="small muted" id="sync">加载中…</p></div>
<div class="stats" id="stats"></div><div id="content"></div>
<p><a href="phygen_acd_smoke.html">PhyGenBench · 10 条 A/C/D 首帧对照与实时视频 →</a> · <a href="index.html">主实验总览</a></p>
<p class="small muted">分析使用 GPT-6.1-Sol / GPT-5.6-Sol / GPT-5.6-Terra / GPT-5.6-Luna / DeepSeek-V4.1-Flash，均为免费模型；进度按各矩阵 status.json 统计，暂停条件显示为 paused。</p>
</div><script>
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>typeof n==='number'?n.toLocaleString():'—',pct=(a,b)=>b?Math.min(100,100*a/b):0;
function ago(s){if(!s)return'—';const t=new Date(s).getTime();if(!isFinite(t))return'—';const d=Math.max(0,(Date.now()-t)/1000);if(d<90)return Math.round(d)+' 秒前';if(d<5400)return Math.round(d/60)+' 分钟前';return Math.round(d/3600)+' 小时前'}
function render(d){$('sync').textContent='更新：'+new Date(d.updated_utc).toLocaleString();const t=d.totals;$('stats').innerHTML=[['有效交付',t.complete],['运行中',t.running],['待生成',t.pending],['失败/终止',t.exhausted],['总 case',t.total]].map(([k,v])=>'<div class="stat"><b>'+fmt(v)+'</b><span class="small muted">'+k+'</span></div>').join('');
$('content').innerHTML=d.conditions.map(c=>{const p=pct(c.counts.complete,c.total),badge=c.planned?'<span class="badge planned">planned</span>':c.paused?'<span class="badge paused">paused</span>':'<span class="badge active">running</span>';const rows=(c.models||[]).map(m=>'<tr><td><a href="'+esc(c.page)+'?suite='+encodeURIComponent(c.id)+'&track='+encodeURIComponent(c.track)+'&model='+encodeURIComponent(m.key)+'">'+esc(m.label)+'</a></td><td class="num">'+fmt(m.complete)+' / '+fmt(m.total)+'</td><td class="num">'+fmt(m.running)+'</td><td class="num">'+fmt(m.pending)+'</td><td class="num">'+fmt(m.exhausted)+'</td><td class="num">'+(m.mean_duration_seconds?Math.round(m.mean_duration_seconds/60)+' 分钟':'—')+'</td><td class="small muted">'+ago(m.latest_completion?.[0])+'</td></tr>').join('');return '<div class="card"><div class="row"><div><b>'+esc(c.title)+'</b> '+badge+'<div class="small muted">'+esc(c.question)+' · '+esc(c.track)+' · '+fmt(c.cases)+' prompts/model</div></div><div class="small muted">更新：'+ago(c.updated_utc)+'</div></div><div class="bar"><i style="width:'+p+'%"></i></div><div class="row small"><span>完成 <b>'+fmt(c.counts.complete)+'</b> / '+fmt(c.total)+'</span><span>运行 '+fmt(c.counts.running)+' · 待生成 '+fmt(c.counts.pending)+' · 终止 '+fmt(c.counts.exhausted)+'</span></div>'+(rows?'<details><summary class="small">按模型展开</summary><table><thead><tr><th>模型</th><th>完成</th><th>运行</th><th>待生成</th><th>终止</th><th>平均耗时</th><th>最近交付</th></tr></thead><tbody>'+rows+'</tbody></table></details>':'')+'</div>'}).join('')}
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
