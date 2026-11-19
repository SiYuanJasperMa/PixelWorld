import json
from tools.render_bench_scale_charts import score_specs
from tools.phygen_protocol import MAIN_JUDGE_FOLDER,FRAME_SAMPLING_REVISION


def test_cost_chart_uses_corrected_phygen_score_location(tmp_path):
    folder=tmp_path/'evaluation'/MAIN_JUDGE_FOLDER;folder.mkdir(parents=True)
    summary={'frame_sampling_revision':FRAME_SAMPLING_REVISION,'track':'vision-agent',
             'models':{'gpt61-sol':{'cases':160,'mean_0_1':0.75,'mean_1_5':4}}}
    (folder/'summary.json').write_text(json.dumps(summary))
    costs={'phygenbench':{'gpt61-sol':{'mean_cost_usd':1,'accepted_cases':160,'cache_usage_complete_cases':160}}}
    spec=score_specs(tmp_path,costs)['phygenbench']
    assert any(row[0]=='gpt61-sol' and row[3]==0.75 for row in spec['models'])
