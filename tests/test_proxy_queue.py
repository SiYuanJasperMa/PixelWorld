import ast
import json
from pathlib import Path


def queue_function():
    source=Path(__file__).resolve().parents[2]/'traex_api_proxy/responses_proxy.py'
    tree=ast.parse(source.read_text())
    node=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='queue_aware_events')
    namespace={'json':json,'sse_events':lambda response:iter(response.events)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['queue_aware_events']


class Response:
    def __init__(self,events=(),status=200):
        self.events=events;self.status_code=status;self.closed=False
    def close(self):self.closed=True


def test_pre_output_4055_waits_in_same_request():
    first=Response([('error',json.dumps({'code':4055}))])
    second=Response([('output',json.dumps({'response':'ok'}))])
    waits=[];reopens=[]
    def reopen():reopens.append(True);return second
    events=list(queue_function()(first,reopen,waits.append,60))
    assert [x[0]for x in events]==['queue_wait','output']
    assert waits==[60]and len(reopens)==1 and first.closed and second.closed


def test_post_output_rejection_never_replays_tool_calls():
    response=Response([('output',json.dumps({'tool_calls':[{'id':'call1'}]})),('error',json.dumps({'code':4055}))])
    def forbidden(*args):raise AssertionError('partial output must not be replayed')
    events=list(queue_function()(response,forbidden,forbidden,60))
    assert [x[0]for x in events]==['output','error']


def test_http_429_before_output_waits():
    first=Response(status=429);second=Response([('output',json.dumps({'response':'ok'}))]);waits=[]
    events=list(queue_function()(first,lambda:second,waits.append,60))
    assert [x[0]for x in events]==['queue_wait','output']and waits==[60]
