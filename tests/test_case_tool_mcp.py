import base64
import subprocess

from pixelworld.case_tool_mcp import CaseTools, handle, tool_list


def stub(tmp_path, monkeypatch, result):
    case=tmp_path/'case';case.mkdir();runtime=tmp_path/'runtime';runtime.mkdir()
    tool=CaseTools(case,runtime);calls=[]
    def execute(args,data=None):
        calls.append((args,data));return result
    monkeypatch.setattr(tool,'_execute',execute)
    return tool,calls


def test_mcp_exposes_only_named_case_tools_and_rejects_extra_arguments(tmp_path,monkeypatch):
    tool,calls=stub(tmp_path,monkeypatch,subprocess.CompletedProcess([],0,b'',b''))
    assert {t['name'] for t in tool_list()}=={'read','write','edit','bash','view_image'}
    assert tool.call('read',{'path':'local.txt','workspace':'/other'})['isError']
    assert tool.call('bash',{'command':'pwd','cwd':'/other'})['isError']
    assert tool.call('read',{'path':'../neighbor.txt'})['isError']
    assert not calls


def test_mcp_view_image_retains_exact_bytes(tmp_path,monkeypatch):
    raw=b'\x89PNG\r\n\x1a\nimage-payload'
    tool,calls=stub(tmp_path,monkeypatch,subprocess.CompletedProcess([],0,raw,b''))
    result=handle(tool,{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'view_image','arguments':{'path':'frame.png'}}})
    assert result['id']==4 and result['result']['isError'] is False
    image=result['result']['content'][0]
    assert image['mimeType']=='image/png' and base64.b64decode(image['data'])==raw
    assert calls[0][0][-2]==str(tmp_path/'case/frame.png')


def test_mcp_handshake_does_not_admit_other_protocol_operations(tmp_path,monkeypatch):
    tool,_=stub(tmp_path,monkeypatch,subprocess.CompletedProcess([],0,b'',b''))
    init=handle(tool,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}})
    assert init['result']['protocolVersion']=='2024-11-05'
    assert handle(tool,{'jsonrpc':'2.0','method':'notifications/initialized'}) is None
    assert handle(tool,{'jsonrpc':'2.0','id':2,'method':'resources/read'})['error']['code']==-32601
