import json
from pathlib import Path
import shutil
import subprocess

import pytest


ASSET = Path(__file__).resolve().parents[1]/'pixelworld/assets/results_browser.js'


def evaluate(script):
    if not shutil.which('node'):
        pytest.skip('Node is required for the browser route tests')
    result = subprocess.run(['node', '-e', 'const ui=require('+json.dumps(str(ASSET))+');'+script],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_legacy_model_links_select_current_hf_suite_and_ignore_ablations():
    result = evaluate("""
const catalogue={groups:[
 {suite:'physics_iq_720p24fps',model_key:'gpt56-sol',track:'vision-agent',path:'physics_iq_720p24fps/gpt56-sol/vision-agent/index.json'},
 {suite:'physics_iq_res1080',model_key:'gpt56-sol',track:'vision-agent',path:'physics_iq_res1080/gpt56-sol/vision-agent/index.json'}]};
const benchmark=ui.benchmarkFor(null,'physics_iq_baseline.html');
console.log(JSON.stringify({benchmark,groups:ui.selectGroups(catalogue,benchmark,'gpt56-sol'),
 poster:ui.assetURL('assets/poster.png'),rejected:ui.assetURL('https://private.example/preview.mp4')}));
""")
    assert result['benchmark'] == 'physics_iq'
    assert len(result['groups']) == 1 and result['groups'][0]['suite'] == 'physics_iq_720p24fps'
    assert '/resolve/main/data/assets/' in result['poster']
    assert result['rejected'] is None


def test_native_suite_merge_keeps_one_case_and_all_distinct_attempts():
    result = evaluate("""
const groups=['physics_iq_native_astra62_smoke','physics_iq_native_astra62_quota5','physics_iq_res1080'].map(suite=>({
 suite,model_key:'gpt62-astra-native',track:'vision-agent',path:suite+'/gpt62-astra-native/vision-agent/index.json'}));
const selected=ui.selectGroups({groups},'physics_iq','gpt62-astra-native');
const indexes=[{suite:selected[0].suite,cases:{'0001':[{detail:'a/0001.json',attempt:1,status:'complete',hf_detail_ready:true}]}},
 {suite:selected[1].suite,cases:{'0001':[{detail:'b/0001.json',attempt:1,status:'failed'}],
 '0002':[{detail:'b/0002.json',attempt:1,status:'running',hf_detail_ready:false}]}},
 {suite:selected[0].suite,cases:{'0001':[{detail:'a/0001.json',attempt:1,status:'complete'}]}}];
console.log(JSON.stringify({selected,rows:ui.mergeCases(indexes),usage:ui.readUsage({output_tokens:0,cached_input_tokens:12},{input_tokens:20})}));
""")
    assert len(result['selected']) == 2
    assert len(result['rows']) == 2
    assert len(result['rows'][0]['history']) == 2
    assert result['rows'][0]['current']['status'] == 'complete'
    assert result['rows'][1]['current']['status'] == 'running'
    assert result['usage']['output_tokens'] == 0 and result['usage']['input_tokens'] == 20


def test_ready_delivery_wins_over_unsynced_reuse_without_discarding_history():
    result = evaluate("""
const rows=ui.mergeCases([{suite:'physics_iq_720p24fps',cases:{'0001':[
 {detail:'a/ready.json',status:'complete',reused:false,hf_detail_ready:true},
 {detail:'a/reused.json',status:'complete',reused:true,hf_detail_ready:false}]}}]);
console.log(JSON.stringify(rows));
""")
    assert result[0]['current']['detail'] == 'a/ready.json'
    assert len(result[0]['history']) == 2 and result[0]['history'][1]['reused'] is True


def test_unscoped_model_does_not_merge_different_benchmarks():
    result = evaluate("""
const groups=['physics_iq_720p24fps','pcve_standard720p24fps'].map(suite=>({
 suite,model_key:'gpt56-sol',track:'vision-agent',path:suite+'/gpt56-sol/vision-agent/index.json'}));
console.log(JSON.stringify(ui.selectGroups({groups},null,'gpt56-sol')));
""")
    assert result == []
