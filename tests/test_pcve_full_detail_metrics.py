import json
from tools import live_evaluation_data as live
from tools.build_formal_dashboard import JS
from pixelworld import pcve


def test_pcve_score_exposes_all_eight_official_metrics():
    overall={'pes':.2,'traj_px':10,'mask_iou':.4,'psnr':30,'ssim':.9,'lpips':.1,'clip_sim':.8}
    summary={'complete_official129':True,'case_count':129,'model':'m','track':'vision-agent',
             'source_revision':pcve.EVALUATOR_REVISION,'dataset_revision':pcve.DATASET_REVISION,
             'scores':{'overall':overall,'fvd':100}}
    result=live.pcve_score(summary,'m','vision-agent')
    assert set(result['metrics'])=={'PES','TE px','Mask IoU','PSNR','SSIM','LPIPS','CLIP','FVD'}
    assert result['metrics']['PES']==.2
    assert result['sam2_native_postprocessing'] is False
    assert result['published_table1_comparability_verified'] is False
    summary.update(sam2_native_postprocessing=True,sam2_native_extension_sha256='verified-binary')
    assert live.pcve_score(summary,'m','vision-agent')['sam2_native_extension_sha256']=='verified-binary'


def test_current_pcve_does_not_publish_pre_native_score_as_primary(tmp_path,monkeypatch):
    (tmp_path/'evaluation/pcve_standard720p24fps_scheduler/m/vision-agent').mkdir(parents=True)
    (tmp_path/'generations/pcve_standard720p24fps').mkdir(parents=True)
    (tmp_path/'generations/pcve_standard720p24fps/status.json').write_text(json.dumps({
        'groups':{'m/vision-agent':{'counts':{'complete':129}}}}))
    summary={'complete_official129':True,'case_count':129,'model':'m','track':'vision-agent',
             'source_revision':pcve.EVALUATOR_REVISION,'dataset_revision':pcve.DATASET_REVISION,
             'scores':{'overall':{'pes':.2,'traj_px':10,'mask_iou':.4,'psnr':30,
                                  'ssim':.9,'lpips':.1,'clip_sim':.8},'fvd':100}}
    cohort=tmp_path/'evaluation/pcve_standard720p24fps_scheduler/m/vision-agent'
    (cohort/'summary.json').write_text(json.dumps(summary))
    monkeypatch.setattr(live,'COHORTS',{'pcve_standard720p24fps':('PCVE','pcve_standard720p24fps_scheduler',129)})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    row=live.collect(tmp_path)['cohorts'][0]
    assert not row['full_scope_scored'] and row['score'] is None
    assert row['previous_environment_score']['metrics']['PES']==.2
    native=cohort/'sam2_native_v2';native.mkdir()
    summary.update(sam2_native_postprocessing=True,sam2_native_extension_sha256='verified-binary')
    (native/'summary.json').write_text(json.dumps(summary))
    row=live.collect(tmp_path)['cohorts'][0]
    assert row['full_scope_scored'] and row['score']['sam2_native_postprocessing']


def test_full_paper_reference_table_is_published_separately(tmp_path,monkeypatch):
    (tmp_path/'evaluation').mkdir()
    ref={'source':'https://arxiv.org/pdf/2609.35134','scope':'Table 1, full 129',
         'rows':[{'label':'fixture','metrics':{'PES':.1}}]}
    (tmp_path/'evaluation/pcve_paper_reference_table.json').write_text(json.dumps(ref))
    monkeypatch.setattr(live,'COHORTS',{})
    monkeypatch.setattr(live,'live_commands',lambda:[])
    assert live.collect(tmp_path)['benchmark_references']['pcve']==ref


def test_frontend_keeps_main_metric_and_provides_full_detail_table():
    assert "pcve:'PES'" in JS
    assert 'pcveMetricTable(b)' in JS and 'pcveMetricTable(b,model)' in JS
    assert 'FVD' in JS and 'TE px' in JS
    assert 'Table 2' not in JS
