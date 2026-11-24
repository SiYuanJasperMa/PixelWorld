"""Read external baseline provenance and merge validated same-protocol reruns."""
from copy import deepcopy
from pathlib import Path

from tools.live_evaluation_data import finite, read, robo_score, pcve_score
from tools.phygen_protocol import THREE_STAGE_JUDGE_FOLDER,THREE_STAGE_REVISION


def video_baselines(bundle: Path) -> list[dict]:
    raw = read(bundle/'evaluation/video_baselines.json')
    rows = deepcopy(raw.get('models', []))
    if not rows:
        rows = [{'key':'seedance25','label':'Seedance 2.5','scores':{}},
                {'key':'minimax-h3','label':'MiniMax H3','scores':{}}]
    seedance = next((row for row in rows if row.get('key') == 'seedance25'), None)
    local_root=bundle/'baselines/minimax_h3_local'
    local_progress=read(local_root/'progress.json')
    if local_progress:
        full129=local_progress.get('total')==129
        local={'key':'minimax-h3-local','label':'MiniMax H3 · 本地全量'if full129 else'MiniMax H3 · 本地校准','scores':{},
               'progress':{k:local_progress[k]for k in ('phase','completed','total','failed_cases','updated_utc','allocated_gpus','waiting_gpu_shards')if k in local_progress}}
        summary=read(local_root/('evaluation'if full129 else'evaluation_subset')/'summary.json')
        overall=summary.get('scores',{}).get('overall',{})
        full_score=pcve_score(summary,'minimax-h3-local','video-edit')if full129 else None
        if full_score and full_score['sam2_native_postprocessing']:
            local['scores']['pcve']={'value':full_score['metrics']['PES'],'kind':'matched_rerun',
                'judge':'PCVE released evaluator','cases':129,
                'note':'Local full129; official H3 checkpoint; commercial API equivalence unverified'}
        elif not full129 and summary.get('case_count')==local_progress.get('total')and finite(overall.get('pes')):
            local['scores']['pcve']={'value':overall['pes'],'kind':'calibration_subset',
                'judge':'PCVE released evaluator','cases':summary['case_count'],
                'note':'Local official H3 checkpoint; fixed calibration subset; not full129'}
        rows.append(local)
    phygen_root=bundle/'baselines/minimax_h3_phygen'
    phygen_progress=read(phygen_root/'progress.json')
    if phygen_progress:
        if (phygen_root/'backfill').exists():
            phygen_progress['completed']=sum(read(f).get('status')=='completed'for f in(phygen_root/'cases').glob('phygen_*/generation.json'))
            main_gpu=read(phygen_root/'gpu_state.json');donor_gpu=read(phygen_root/'backfill/gpu_state.json')
            phygen_progress['allocated_gpus']=(len(main_gpu.get('gpus',[]))if main_gpu.get('phase')=='running'else 0)+(len(donor_gpu.get('gpus',[]))if donor_gpu.get('phase')=='running'else 0)
        local=next((row for row in rows if row.get('key')=='minimax-h3-local'),None)
        if local is None:
            local={'key':'minimax-h3-local','label':'MiniMax H3 · 本地全量','scores':{}}
            rows.append(local)
        progress_fields=('phase','completed','total','failed_cases','updated_utc','allocated_gpus',
            'evaluation_completed','evaluation_total')
        local.setdefault('benchmark_progress',{})['phygenbench']={
            k:phygen_progress[k]for k in progress_fields if k in phygen_progress}
        result=read(bundle/'evaluation/minimax_h3_phygen_gpt55_three_stage_mcq_v1/summary.json')
        entry=result.get('models',{}).get('minimax-h3-local',{})
        if result.get('full160')is True and result.get('version')==THREE_STAGE_REVISION and entry.get('cases')==160 and not result.get('failures')and finite(entry.get('mean')):
            local['scores']['phygenbench']={'value':entry['mean'],'kind':'matched_rerun',
                'judge':'GPT-5.5 three-stage','cases':160,'note':'Full local H3 T2V; same MCQ protocol as Seedance and coding agents'}
    if seedance is not None:
        result = read(bundle/'evaluation'/THREE_STAGE_JUDGE_FOLDER/'summary.json')
        entry = result.get('models',{}).get('seedance25-v1',{})
        if entry.get('cases') == 160 and result.get('version')==THREE_STAGE_REVISION and finite(entry.get('mean')):
            seedance.setdefault('scores',{})['phygenbench'] = {'value':entry['mean'],
                'kind':'matched_rerun','judge':'GPT-5.5 three-stage','cases':160,'note':'Same MCQ protocol as coding agents'}
        else:seedance.setdefault('scores',{}).pop('phygenbench',None)
        for folder in sorted((bundle/'evaluation').glob('seedance25_robojudge_shared50_*')):
            result = robo_score(read(folder/'summary.json'),read(folder/'plan.json'),
                                read(folder/'execution.json'),'seedance25','i2v')
            if result:
                seedance.setdefault('scores',{})['robojudge'] = {'value':result['metrics']['Mean'],
                    'kind':'matched_rerun','judge':'RoboJudge-9B','cases':50,'note':'provided shared-50 I2V videos'}
    # Only fields intended for the public page leave this helper.
    output=[]
    for row in rows:
        scores={key:{name:value for name,value in entry.items() if name in ('value','kind','judge','cases','note')}
                for key,entry in row.get('scores',{}).items() if finite(entry.get('value'))}
        item={name:row[name] for name in ('key','label','price','progress','benchmark_progress') if name in row}
        item['scores']=scores
        output.append(item)
    return output
