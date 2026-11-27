import json
from pixelworld.config import load_config
from pixelworld.retries import authorize_near_complete


def test_temporary_migration_aliases_do_not_count_as_model_attempts(tmp_path):
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'inputs_root':'inputs','runs_root':'runs','case_ids':['0001'],
        'max_attempts':1,'max_total_attempts':1,
        'models':[{'key':'fixture','model':'fixture','tracks':['text']}]}))
    config=load_config(path);parent=config.runs_root/'fixture/text';run=parent/'case_0001_attempt_01'
    run.mkdir(parents=True)
    (run/'run_manifest.json').write_text(json.dumps({'status':'failed','retryable':True,'validation':{'pass':False}}))
    (parent/'case_0001_attempt_01.migration_link_20261126').symlink_to(run,target_is_directory=True)
    (parent/'case_0001_attempt_09.verified_migration_delete_20261126').symlink_to(run,target_is_directory=True)
    result=authorize_near_complete(config,min_fraction=0,max_missing=None,until_first_delivery=True,model_keys={'fixture'},apply=True)
    assert result['authorized_cases']==1
    ledger=json.loads((config.runs_root/'retry_authorizations.json').read_text())
    assert ledger['cases']['fixture/text/0001'][-1]['start_attempt']==2
