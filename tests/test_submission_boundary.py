import json

from PIL import Image
from test_submission_tool import fixture
from pixelworld.submission import submit
from pixelworld.submission_boundary import accept_boundary, verify_boundary, completed_usage, STOP_MESSAGE


def setup(tmp_path):
    run,_=fixture(tmp_path)
    path=run/'run_manifest.json';manifest=json.loads(path.read_text());manifest['harness_variant']='flash-submit-boundary'
    path.write_text(json.dumps(manifest));assert submit(run)['accepted']
    return run


def test_boundary_requires_explicit_submission_and_stable_frame_identity(tmp_path):
    run=setup(tmp_path)
    assert accept_boundary(run/'workspace','actual-request-hash')['pre_upstream']
    assert not verify_boundary(run)  # No real native boundary-stop event yet.
    with (run/'logs/events.jsonl').open('a')as file:
        file.write(json.dumps({'type':'turn.failed','error':{'message':STOP_MESSAGE}})+'\n')
    assert verify_boundary(run)
    Image.new('RGB',(24,16),'blue').save(run/'workspace/frames/01.png')
    assert not verify_boundary(run)
    assert accept_boundary(run/'workspace','next-request') is None


def test_completed_gateway_usage_requires_all_records_and_native_counter_agreement(tmp_path):
    run=setup(tmp_path);accept_boundary(run/'workspace','hash')
    folder=run/'logs/submission_requests';folder.mkdir()
    path=folder/'one.json';path.write_text(json.dumps({'phase':'completed','response_id':'real-api-response',
        'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':70},
                 'output_tokens_details':{'reasoning_tokens':4}}}))
    raw={'status':'missing','source':'actual failed CLI terminal'}
    trace=run/'logs/full_rollout.jsonl'
    trace.write_text(json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{
        'total_token_usage':{'input_tokens':100,'output_tokens':10}}}})+'\n')
    result=completed_usage(run,raw)
    assert result['input_tokens']==100 and result['output_tokens']==10
    assert result['cached_input_tokens']==70 and result['reasoning_output_tokens']==4
    assert result['raw_terminal_usage']==raw and not result['inflight_usage_unknown']
    trace.write_text(trace.read_text().replace('100','99'))
    assert completed_usage(run,raw)==raw
    path.write_text('{"phase":"started"}')
    assert completed_usage(run,raw)==raw
