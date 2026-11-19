#!/usr/bin/env python3
"""Render one cost-quality draft chart per benchmark for the public homepage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK, MUTED = "#182333", "#64748b"
BENCH = {
    "physics_iq": {"title":"Physics-IQ", "ylabel":"Verified score (%)", "ylim":(0, 60)},
    "robojudge": {"title":"RoboJudge shared-50", "ylabel":"RoboJudge Mean (1-5)", "ylim":(1, 5)},
    "pcve": {"title":"PCVE-RigidBench", "ylabel":"PES", "ylim":(-0.15, 0.02)},
    "phygenbench": {"title":"PhyGenBench", "ylabel":"Overall (0-1; GPT-5.5 adaptation)", "ylim":(0, 1)},
}
MODELS = {
    "gpt61-sol": ("GPT-6.1-Sol", "#2563eb"),
    "gpt6-astra": ("GPT-6-Astra", "#dc2626"),
    "gpt56-sol": ("GPT-5.6-Sol", "#0891b2"),
    "gpt56-terra": ("GPT-5.6-Terra", "#7c3aed"),
    "gpt56-luna": ("GPT-5.6-Luna", "#059669"),
    "deepseek-v41": ("DeepSeek-V4.1-Flash", "#d97706"),
    "seed21-pro": ("Seed-2.1-Pro", "#64748b"),
}


def score_specs(bundle: Path, costs: dict) -> dict:
    from tools.live_evaluation_data import read, physics_score, robo_score, pcve_score, phygen_score
    from tools.phygen_protocol import MAIN_JUDGE_FOLDER
    from tools.baseline_data import video_baselines
    folders={'physics_iq':'physics_720p24fps_full_scheduler',
             'robojudge':'robojudge_main720p24fps_scheduler',
             'pcve':'pcve_standard720p24fps_scheduler'}
    models=dict(MODELS)
    for model in read(bundle/'bundle.json').get('models',[]):
        key=model['key']
        if key not in models:
            models[key]=(model.get('display_name',model['model'].split('/')[-1]),'#475569')
    specs={}
    for name,meta in BENCH.items():
        spec={**meta,'models':[],'baselines':[],'matched_baselines':[],'excluded_baselines':[],
              'pending':'Full-cohort scores only; missing cost counters omitted'}
        for key,(label,color) in models.items():
            root=bundle/'evaluation'/folders.get(name,'')/key/'vision-agent'
            if name=='physics_iq':score=physics_score(read(root/'scores.json'),key,'vision-agent')
            elif name=='robojudge':score=robo_score(read(root/'summary.json'),read(root/'plan.json'),read(root/'execution.json'),key,'vision-agent')
            elif name=='pcve':score=pcve_score(read(root/'summary.json'),key,'vision-agent')
            else:score=phygen_score(read(bundle/'evaluation'/MAIN_JUDGE_FOLDER/'summary.json'),key,'vision-agent')
            cost_row=costs.get(name,{}).get(key,{})
            cost=cost_row.get('mean_cost_usd')
            metric={'physics_iq':'Verified','robojudge':'Mean','pcve':'PES','phygenbench':'Overall'}[name]
            if (score and cost is not None and cost_row.get('accepted_cases')==score['scope']
                    and cost_row.get('cache_usage_complete_cases')==score['scope']):
                spec['models'].append((key,label,cost,score['metrics'][metric],color))
        for baseline in video_baselines(bundle):
            entry=baseline.get('scores',{}).get(name,{})
            price=baseline.get('price',{}).get('usd_per_video')
            if entry.get('value') is not None and price is not None:
                if name=='phygenbench' and entry.get('kind')!='matched_rerun':
                    spec['excluded_baselines'].append(baseline['label'])
                    continue
                field='matched_baselines' if entry.get('kind')=='matched_rerun' else 'baselines'
                spec[field].append((baseline['label'],price,entry['value']))
        specs[name]=spec
    return specs


def render(name: str, spec: dict, output: Path, costs: dict | None = None, seedance: dict | None = None) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(7.2, 5.0), facecolor="white")
    ax.set_xscale("log")
    prices=[row[2] for row in spec['models']]+[row[1] for row in spec['baselines']+spec.get('matched_baselines',[])]
    scores=[row[3] for row in spec['models']]+[row[2] for row in spec['baselines']+spec.get('matched_baselines',[])]
    positive=[value for value in prices if value>0]
    ax.set_xlim((min(positive)/1.6,max(positive)*1.6) if positive else (0.3,40))
    low,high=spec['ylim']
    if scores:
        pad=max((max(scores)-min(scores))*.12,(high-low)*.05)
        low=min(low,min(scores)-pad);high=max(high,max(scores)+pad)
    ax.set_ylim(low,high)
    ax.set_xlabel("Reference-price cost / video (US$, reported cache)" if costs else "Estimated API cost / video (US$, 70% cache)", color=INK)
    ax.set_ylabel(spec["ylabel"], color=INK)
    ax.grid(axis="y", color="#edf1f5")
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#dce3eb")
    for key, label, cost, score, color in spec["models"]:
        if costs:
            record = costs.get(name, {}).get(key, {})
            measured = record.get('mean_cost_usd')
            if measured is None:
                continue
            cost = measured
        ax.scatter(cost, score, s=95, color=color, edgecolor="white", linewidth=1.2, zorder=4)
        offset=(0,-20) if key=='gpt56-sol' and name=='phygenbench' else (0,10)
        ax.annotate(label, (cost, score), xytext=offset, textcoords="offset points",
                    ha="center", fontsize=7.5, color=color, weight="bold")
    for label, cost, score in spec["baselines"]:
        ax.scatter(cost, score, s=110, marker="*", color="#475569", edgecolor="white", linewidth=1.0, zorder=5)
        ax.annotate(label + "*", (cost, score), xytext=(0, -15), textcoords="offset points",
                    ha="center", fontsize=7.5, color="#475569")
    for label,cost,score in spec.get('matched_baselines',[]):
        ax.scatter(cost, score, s=90, marker='D', color='#475569', edgecolor='white', zorder=5)
        ax.annotate(label+' · matched judge', (cost, score), xytext=(0, 10),
                    textcoords='offset points', ha='center', fontsize=7.5, color='#475569')
    ax.set_title(spec["title"] + " · cost vs score", loc="left", fontsize=13, weight="bold", color=INK)
    fig.text(0.02, 0.012,
             ("DRAFT: reported cache; one accepted delivery/case; shared reference token prices; retries excluded; "
              if costs else "DRAFT: main 720p/24 FPS Vision; agent cost from successful runs at 70% cache; ")
             + spec["pending"] + ("; different-judge baselines omitted" if spec.get('excluded_baselines') else "") + ".",
             fontsize=6.5, color=MUTED)
    fig.text(0.02, 0.036,
             "* Reference score; diamond: matched re-evaluation. Video prices are 5-second tariff references. Token reference: $10/$1/$50 per 1M input/cache/output.",
             fontsize=6.2, color=MUTED)
    fig.tight_layout(rect=(0, 0.085, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    fig.savefig(output.with_suffix(".png"), dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-report", type=Path, help="Cohort report with measured cache and explicit token price assumptions")
    parser.add_argument("--bundle", type=Path, help="Run bundle containing external full-cohort scores and baseline provenance")
    parser.add_argument("--seedance-summary", type=Path, help="Completed Seedance v1 evaluation with the same GPT-5.5 judge")
    args = parser.parse_args()
    costs = json.loads(args.cost_report.read_text()) if args.cost_report else None
    seedance = None
    if args.seedance_summary:
        summary = json.loads(args.seedance_summary.read_text())
        entry = summary.get('models', {}).get('seedance25-v1', {})
        if entry.get('cases') == 160 and not summary.get('failures'):
            seedance = entry
    bundle=args.bundle or (args.cost_report.parent.parent if args.cost_report else None)
    if bundle is None or costs is None:
        parser.error('--bundle and --cost-report are required for evidence-based charts')
    specs=score_specs(bundle.resolve(),costs)
    for name, spec in specs.items():
        render(name, spec, args.output / f"{name}_scale", costs, seedance)
    print("rendered", len(specs), "charts to", args.output)
