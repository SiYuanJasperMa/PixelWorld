import json
from pathlib import Path

from tools.cost_report import cohort_report


PRICE = {'input': 10, 'cached_input': 1, 'output': 50}


def delivery(root: Path, case, attempt, cache):
    path = root / 'physics_iq/model/vision-agent' / f'case_{case}_attempt_{attempt}' / 'run_manifest.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'status':'complete', 'validation':{'pass':True},
        'model_key':'model', 'case_id':case, 'token_usage':{
            'input_tokens':1000000, 'cached_input_tokens':cache, 'output_tokens':1000000}}))
    return path


def test_cost_selects_earliest_numeric_attempt_and_reuse(tmp_path):
    delivery(tmp_path, '0001', 10, 0)
    delivery(tmp_path, '0001', 2, 1000000)
    historical = delivery(tmp_path, 'historical', 1, 1000000)
    assert cohort_report(tmp_path, {'model':PRICE})['physics_iq']['model']['mean_cost_usd'] == 51
    # The registry selects the audited historical delivery for the same case.
    (tmp_path / 'physics_iq/reuse_registry.json').write_text(json.dumps({'entries':[
        {'model_key':'model', 'track':'vision-agent', 'case_id':'0001', 'source':str(historical.parent)}]}))
    rows = cohort_report(tmp_path, {'model':PRICE})['physics_iq']['model']
    assert rows['accepted_cases'] == 2
    assert rows['mean_cost_usd'] == 51


def test_missing_cache_is_not_charged_as_zero_hit(tmp_path):
    delivery(tmp_path, '0001', 1, 1000000)
    delivery(tmp_path, '0002', 1, None)
    row = cohort_report(tmp_path, {'model':PRICE})['physics_iq']['model']
    assert row['cache_usage_missing_cases'] == 1
    assert row['mean_cost_usd'] is None
    assert row['covered_mean_cost_usd'] == 51
