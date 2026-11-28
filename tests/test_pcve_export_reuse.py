import json
from types import SimpleNamespace

from pixelworld import pcve
from pixelworld.util import sha256


def export_fixture(tmp_path,monkeypatch):
    runs=tmp_path/'runs';inputs=tmp_path/'inputs';case='pcve_0000';baseline='m'
    run=runs/'model/vision-agent/case_0000_attempt1';run.mkdir(parents=True)
    manifest={'status':'complete','validation':{'pass':True},'case_id':case,'attempt':1,'media_contract':{}}
    (run/'run_manifest.json').write_text(json.dumps(manifest))
    (inputs/case).mkdir(parents=True)
    (inputs/case/'preparation_record.json').write_text(json.dumps({
        'scene':'scene','official_case_id':'edit','global_id':'scene/edit'}))
    config=SimpleNamespace(runs_root=runs,inputs_root=inputs,model=lambda _:True)
    monkeypatch.setattr(pcve,'load_config',lambda _:config)
    old=tmp_path/'old';video=old/baseline/'videos/scene/edit.mp4';video.parent.mkdir(parents=True)
    video.write_bytes(b'accepted-export')
    (old/baseline/'pixelworld_submission.json').write_text(json.dumps({'rows':[{
        'case_id':case,'output':str(video),'source_manifest_sha256':sha256(run/'run_manifest.json'),
        'export_sha256':sha256(video)}]}))
    return old,video,run


def test_native_re_evaluation_reuses_matching_accepted_export(tmp_path,monkeypatch):
    old,video,run=export_fixture(tmp_path,monkeypatch)
    def unexpected(*_):raise AssertionError('Matching export must not be re-encoded')
    monkeypatch.setattr(pcve,'encode_pixels',unexpected)
    result=pcve.export(tmp_path/'config','model','vision-agent',tmp_path/'data','m',tmp_path/'new',reuse_root=old)
    assert result['rows'][0]['reused_export']
    assert (tmp_path/'new/m/videos/scene/edit.mp4').resolve()==video.resolve()


def test_changed_export_is_rebuilt_without_overwriting_original(tmp_path,monkeypatch):
    old,video,run=export_fixture(tmp_path,monkeypatch)
    video.write_bytes(b'changed-export')
    def encode(workspace,spec,output):output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(b'new-export')
    monkeypatch.setattr(pcve,'encode_pixels',encode)
    result=pcve.export(tmp_path/'config','model','vision-agent',tmp_path/'data','m',tmp_path/'new',reuse_root=old)
    assert not result['rows'][0]['reused_export']
    assert video.read_bytes()==b'changed-export'
    assert (tmp_path/'new/m/videos/scene/edit.mp4').read_bytes()==b'new-export'


def test_export_includes_audited_delivery_outside_run_directory(tmp_path,monkeypatch):
    old,video,run=export_fixture(tmp_path,monkeypatch)
    relocated=tmp_path/'adopted_run'
    run.rename(relocated)
    runs=tmp_path/'runs'
    (runs/'reuse_registry.json').write_text(json.dumps({
        'schema':'pixelworld-compatible-history-v1','entries':[{
            'model_key':'model','track':'vision-agent','case_id':'pcve_0000',
            'source':str(relocated),'manifest_sha256':sha256(relocated/'run_manifest.json')}]}))
    def unexpected(*_):raise AssertionError('Audited export must be reused, not regenerated')
    monkeypatch.setattr(pcve,'encode_pixels',unexpected)
    result=pcve.export(tmp_path/'config','model','vision-agent',tmp_path/'data','m',tmp_path/'new',reuse_root=old)
    assert len(result['rows'])==1
    assert result['rows'][0]['source_run']==str(relocated)
    assert result['rows'][0]['reused_export']
