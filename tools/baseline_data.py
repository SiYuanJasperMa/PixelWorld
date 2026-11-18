"""Read external baseline provenance and merge validated same-protocol reruns."""
from copy import deepcopy
from pathlib import Path

from tools.live_evaluation_data import finite, read, robo_score


def video_baselines(bundle: Path) -> list[dict]:
    raw = read(bundle/'evaluation/video_baselines.json')
    rows = deepcopy(raw.get('models', []))
    if not rows:
        rows = [{'key':'seedance25','label':'Seedance 2.5','scores':{}},
                {'key':'minimax-h3','label':'MiniMax H3','scores':{}}]
    seedance = next((row for row in rows if row.get('key') == 'seedance25'), None)
    if seedance is not None:
        result = read(bundle/'evaluation/seedance25_phygen_v1_gpt55/summary.json')
        entry = result.get('models',{}).get('seedance25-v1',{})
        if entry.get('cases') == 160 and not result.get('failures') and finite(entry.get('mean_0_1')):
            seedance.setdefault('scores',{})['phygenbench'] = {'value':entry['mean_0_1'],
                'kind':'matched_rerun','judge':'GPT-5.5','cases':160,'note':'v1 T2V; same PixelWorld judge'}
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
        item={name:row[name] for name in ('key','label','price') if name in row}
        item['scores']=scores
        output.append(item)
    return output
