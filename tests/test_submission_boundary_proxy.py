import http.client
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

from test_submission_boundary import setup
from tools.flash_context_proxy import Relay


def server_for(run, tmp_path):
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'abcdef':str(run/'workspace')}))
    server=ThreadingHTTPServer(('127.0.0.1',0),Relay)
    server.registry=registry;server.allowed_root=run.parent;server.local_key='fixture-key'
    server.upstream='http://fixture-upstream/v1';server.stop_on_submission=True
    server.no_history_compaction=True;server.core_tools_only=False
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server


def test_verified_submission_never_starts_another_upstream_call(tmp_path,monkeypatch):
    run=setup(tmp_path);server=server_for(run,tmp_path);calls=[]
    monkeypatch.setattr('tools.flash_context_proxy.requests.request',lambda *args,**kwargs:calls.append(1))
    client=http.client.HTTPConnection('127.0.0.1',server.server_port)
    try:
        client.request('POST','/case/abcdef/v1/responses',body=b'{"input":[]}',
                       headers={'Authorization':'Bearer fixture-key','Content-Type':'application/json'})
        response=client.getresponse();assert response.status==409;response.read()
        assert not calls
        assert json.loads((run/'logs/submission_boundary.json').read_text())['pre_upstream']
    finally:
        client.close();server.shutdown();server.server_close()


def test_completed_response_bytes_are_forwarded_and_actual_usage_recorded(tmp_path,monkeypatch):
    run=setup(tmp_path);(run/'logs/submission_receipt.json').unlink();server=server_for(run,tmp_path)
    raw=b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"api-response","usage":{"input_tokens":100,"output_tokens":10}}}\n\n'
    chunks=iter([raw[:45],raw[45:],b'']);calls=[]
    def forward(*args,**kwargs):
        calls.append(kwargs['data'])
        return SimpleNamespace(status_code=200,headers={'Content-Type':'text/event-stream'},
                               raw=SimpleNamespace(read1=lambda size:next(chunks)),close=lambda:None)
    monkeypatch.setattr('tools.flash_context_proxy.requests.request',forward)
    client=http.client.HTTPConnection('127.0.0.1',server.server_port)
    try:
        client.request('POST','/case/abcdef/v1/responses',body=b'{"input":[]}',
                       headers={'Authorization':'Bearer fixture-key','Content-Type':'application/json'})
        response=client.getresponse();assert response.status==200;assert response.read()==raw
        assert json.loads(calls[0])=={'input':[]}
        records=list((run/'logs/submission_requests').glob('*.json'))
        assert len(records)==1
        record=json.loads(records[0].read_text())
        assert record['phase']=='completed' and record['usage']=={'input_tokens':100,'output_tokens':10}
    finally:
        client.close();server.shutdown();server.server_close()
