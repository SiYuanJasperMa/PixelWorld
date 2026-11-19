"""Render the completed paired smoke from recorded scores and usage only."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from tools.phygen_protocol import FRAME_SAMPLING_REVISION


def build(release):
    root=release/'analysis/phygen_acd_smoke10'
    evidence=json.loads((root/'paired_final_evidence_chronological_v2.json').read_text())
    if evidence['frame_sampling_revision']!=FRAME_SAMPLING_REVISION or len(evidence['case_rows'])!=10:
        raise ValueError('Expected the frozen ten-case chronological report')
    rows=evidence['case_rows'];routes=evidence['routes']
    initial=[]
    for row in rows:
        accepted=[]
        for path in (release/'analysis_generations/phygen_acd_pixelinit/gpt61-sol/vision-agent').glob('case_'+row['id']+'_attempt_*/run_manifest.json'):
            record=json.loads(path.read_text())
            if record['status']=='complete'and record['validation']['pass']:accepted.append(record)
        record=min(accepted,key=lambda r:r['attempt']);initial.append(record)
    initializer={field:statistics.mean(r['token_usage'][field]for r in initial)for field in ['input_tokens','output_tokens']}
    initializer['minutes']=statistics.mean(r['duration_seconds']/60 for r in initial)
    qwen={}
    for route in ['C','D']:
        totals=[]
        for row in rows:
            records=[json.loads(p.read_text())for p in(root/'qwen'/route/row['id']).glob('seed*.render.json')]
            if len(records)!=3:raise ValueError('Expected all three frozen Qwen candidates')
            totals.append(sum(r['elapsed_seconds']for r in records)/60)
        qwen[route]=statistics.mean(totals)
    summary={'frame_sampling_revision':FRAME_SAMPLING_REVISION,'model':'GPT-6.1-Sol','cases':10,
             'routes':routes,'C_pixel_initializer':initializer,'qwen_three_candidates_mean_minutes':qwen,
             'scope':'paired smoke; video-stage tokens separate from pixel initialization and Qwen compute',
             'caveat':'Pre-action check passed C 3/10, D 0/10. This tests these construction routes, not a clean causal effect of photographic realism.'}
    public=root/'public/report';public.mkdir(parents=True,exist_ok=True)
    (public/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    with(public/'paired_cases.csv').open('w',newline='')as stream:
        writer=csv.writer(stream);writer.writerow(['case','dimension','A_physics','C_physics','D_physics','C_init_suitable','D_init_suitable'])
        for row in rows:writer.writerow([row['id'],row['dimension'],*[row['routes'][r]['score_1_5']for r in ['A','C','D']],row['routes']['C']['initialization_suitable'],row['routes']['D']['initialization_suitable']])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none'})
    fig,axes=plt.subplots(1,3,figsize=(13,4.2),facecolor='white')
    colors=['#2563eb','#0d9488','#d97706'];labels=['A\nAgent T2V','C\nPixel → Qwen → I2V','D\nQwen → I2V'];x=np.arange(3)
    for row in rows:
        values=[row['routes'][r]['score_1_5']for r in ['A','C','D']]
        axes[0].plot(x,values,color='#cbd5e1',alpha=.7,lw=1,marker='o',ms=3)
    means=[routes[r]['mean_physical_1_5']for r in ['A','C','D']]
    axes[0].plot(x,means,color='#182333',lw=2.5,zorder=5)
    axes[0].scatter(x,means,c=colors,s=85,edgecolors='white',zorder=6)
    for i,value in enumerate(means):axes[0].annotate(f'{value:.2f}',(i,value),xytext=(0,10),textcoords='offset points',ha='center',weight='bold')
    axes[0].set_ylim(.7,5.7);axes[0].set_ylabel('Physical consistency (1–5)');axes[0].set_title('Paired physical scores · n=10',loc='left',weight='bold')
    for ax,field,title in [(axes[1],'input_tokens','Input tokens'),(axes[2],'output_tokens','Output tokens')]:
        main=[routes[r]['mean_'+field]/1000 for r in ['A','C','D']];extra=[0,initializer[field]/1000,0]
        ax.bar(x,main,color=colors,width=.6,label='Video-stage agent')
        ax.bar(x,extra,bottom=main,color='#94a3b8',width=.6,hatch='//',label='Additional C pixel still')
        for i,(a,b)in enumerate(zip(main,extra)):ax.text(i,a+b+(max(main)+max(extra))*.03,f'{a+b:.1f}k',ha='center',weight='bold')
        ax.set_ylim(0,(max(a+b for a,b in zip(main,extra)))*1.22);ax.set_ylabel('Mean tokens / case (thousands)');ax.set_title(title+' · recorded usage',loc='left',weight='bold')
    handles,legend_labels=axes[2].get_legend_handles_labels()
    fig.legend(handles,legend_labels,frameon=False,loc='lower center',bbox_to_anchor=(.72,.09),ncol=2,fontsize=8)
    for ax in axes:
        ax.set_xticks(x,labels);ax.spines[['top','right']].set_visible(False);ax.spines[['left','bottom']].set_color('#d7dee7');ax.grid(axis='y',color='#edf1f5');ax.set_axisbelow(True)
    fig.suptitle('PhyGenBench · initialization-route diagnostic',x=.06,ha='left',weight='bold',fontsize=15)
    fig.text(.06,.025,'GPT-6.1-Sol · Vision · High · 720p/24 FPS. Qwen GPU inference is separate and unpriced.\nPre-action suitability: C 3/10, D 0/10; no downstream-score selection. A reused its original accepted runs.',fontsize=8,color='#64748b')
    fig.tight_layout(rect=[0,.16,1,.91])
    for ext in ['svg','png']:fig.savefig(public/('paired_summary.'+ext),dpi=180)
    plt.close(fig)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--release',required=True,type=Path)
    args=p.parse_args();print(json.dumps(build(args.release.resolve()),ensure_ascii=False))
