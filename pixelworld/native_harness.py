"""Native Pi execution with original events plus an external audit projection."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .util import atomic_json, utc_now


def runtime_root() -> Path:
    value=os.environ.get('PIXELWORLD_NATIVE_RUNTIME')
    if not value:
        raise RuntimeError('PIXELWORLD_NATIVE_RUNTIME must point to the pinned cloud runtime')
    root=Path(value).resolve()
    if not str(root).startswith('/mnt/bn/'):
        raise ValueError('Native runtime must reside on cloud storage')
    return root


def pi_paths(root: Path) -> tuple[Path,Path,Path]:
    modules=root/'node_modules'
    return (modules/'node-linux-x64/bin/node',
            modules/'@earendil-works/pi-coding-agent/dist/bundle/cli.js',
            modules/'@earendil-works/pi-coding-agent/dist/index.js')


def version() -> str:
    node,cli,_=pi_paths(runtime_root())
    value=subprocess.check_output([str(node),str(cli),'--version'],text=True).strip()
    if value!='1.6.0':raise ValueError('Pi version differs from frozen 1.6.0')
    return 'pi '+value


def api_usage(usage: dict) -> dict:
    # Pi counts cache reads/writes separately from uncached input tokens.
    names=('input','cacheRead','cacheWrite','output')
    if not all(type(usage.get(k)) is int and usage[k]>=0 for k in names):
        raise ValueError('Incomplete native provider usage')
    result={'input_tokens':usage['input']+usage['cacheRead']+usage['cacheWrite'],
            'cached_input_tokens':usage['cacheRead'],
            'cache_creation_input_tokens':usage['cacheWrite'],
            'output_tokens':usage['output']}
    result['total_tokens']=result['input_tokens']+result['output_tokens']
    if type(usage.get('reasoning')) is int:result['reasoning_output_tokens']=usage['reasoning']
    return result


class Projection:
    def __init__(self, session: Path):
        self.session=session.open('a',encoding='utf-8')
        self.totals={};self.requests=set();self.calls={}

    def archive(self,kind,payload):
        self.session.write(json.dumps({'timestamp':utc_now(),'type':kind,'payload':payload},ensure_ascii=False)+'\n')
        self.session.flush()

    def emit(self,event):
        print(json.dumps(event,ensure_ascii=False),flush=True)

    def consume(self,event):
        kind=event.get('type');message=event.get('message') or {}
        if kind=='message_end' and message.get('role')=='assistant':
            rid=message.get('responseId')
            if rid and rid not in self.requests and message.get('stopReason')!='error':
                usage=api_usage(message.get('usage')or{});self.requests.add(rid)
                for key,value in usage.items():self.totals[key]=self.totals.get(key,0)+value
                self.archive('event_msg',{'type':'token_count','info':{
                    'total_token_usage':self.totals,'last_token_usage':usage},
                    'native_response_id':rid,'native_backend':'pi'})
            text='\n'.join(c.get('text','')for c in message.get('content',[])if c.get('type') in ('text','thinking'))
            if text:self.emit({'type':'item.completed','item':{'type':'agent_message','text':text}})
        elif kind=='tool_execution_start':
            cid=event['toolCallId'];name=event['toolName'];args=event.get('args')or{}
            self.calls[cid]=(name,args)
            self.archive('response_item',{'type':'function_call','call_id':cid,'name':name,
                'arguments':json.dumps(args,ensure_ascii=False)})
            item=self.item(cid,name,args)
            self.emit({'type':'item.started','item':item})
        elif kind=='tool_execution_end':
            cid=event['toolCallId'];name,args=self.calls.get(cid,(event.get('toolName','unknown'),event.get('args')or{}))
            result=event.get('result')or{};content=result.get('content')or[]
            blocks=[]
            for block in content:
                if block.get('type')=='image':
                    blocks.append({'type':'input_image','image_url':'data:'+block['mimeType']+';base64,'+block['data']})
                elif block.get('type')=='text':blocks.append({'type':'text','text':block.get('text','')})
            self.archive('response_item',{'type':'function_call_output','call_id':cid,'output':blocks})
            item=self.item(cid,name,args);failed=bool(event.get('isError'))
            item['status']='failed'if failed else'completed'
            if name=='bash':
                item.update(exit_code=1 if failed else 0,
                    aggregated_output='\n'.join(b.get('text','')for b in content if b.get('type')=='text'))
            self.emit({'type':'item.completed','item':item})

    @staticmethod
    def item(cid,name,args):
        if name=='bash':return {'id':cid,'type':'command_execution','command':args.get('command','')}
        if name in ('write','edit'):return {'id':cid,'type':'file_change','changes':[{'path':args.get('path',''),'kind':'update'}]}
        return {'id':cid,'type':'mcp_tool_call','name':name,'arguments':args}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--reasoning',required=True)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--env-key',required=True)
    parser.add_argument('--model-catalog',type=Path)
    parser.add_argument('--track',choices=('text','vision-agent'),required=True)
    args=parser.parse_args()
    root=runtime_root();node,cli,api=pi_paths(root);version()
    workspace=args.workspace.resolve();state=args.state.resolve();logs=workspace.parent/'logs'
    config=state/'pi-config';config.mkdir(parents=True,exist_ok=True)
    thread=str(uuid.uuid4());folder=state/'sessions'/dt.datetime.now(dt.timezone.utc).strftime('%Y/%m/%d')
    folder.mkdir(parents=True,exist_ok=True)
    projection=Projection(folder/('native-'+thread+'.jsonl'))
    projection.archive('session_meta',{'id':thread,'source':'native-pi','cwd':str(workspace)})
    projection.emit({'type':'thread.started','thread_id':thread});projection.emit({'type':'turn.started'})
    context_window=272000
    if args.model_catalog:
        entries=json.loads(args.model_catalog.read_text())['models']
        entry=next((row for row in entries if row.get('slug')==args.model),None)
        if not entry or type(entry.get('context_window')) is not int:
            raise ValueError('The frozen model catalog has no matching context limit')
        context_window=entry['context_window']
    atomic_json(config/'models.json',{'providers':{'pixelworld':{
        'baseUrl':args.base_url,'api':'openai-responses','apiKey':'$'+args.env_key,
        'models':[{'id':args.model,'name':args.model,'reasoning':True,
                   'input':['text','image'] if args.track=='vision-agent'else['text'],
                   'contextWindow':context_window,'maxTokens':32768}]}}})
    python_runtime=Path(sys.executable).resolve().parent.parent
    environment=os.environ.copy();environment.update({
        'PI_CODING_AGENT_DIR':str(config),'XDG_CONFIG_HOME':str(state/'config'),
        'XDG_DATA_HOME':str(state/'data'),'XDG_CACHE_HOME':str(state/'cache'),
        'TMPDIR':str(state/'tmp'),'PIXELWORLD_PI_MODULE':str(api),
        'PIXELWORLD_NATIVE_WORKSPACE':str(workspace),
        'PIXELWORLD_NATIVE_PYTHON_RUNTIME':str(python_runtime),
        'PIXELWORLD_NATIVE_SANDBOX':str(Path(__file__).with_name('native_sandbox.py')),
        'PATH':str(node.parent)+':'+os.environ.get('PATH','')})
    Path(environment['TMPDIR']).mkdir(parents=True,exist_ok=True)
    command=[str(node),str(cli),'--print','--mode','json','--provider','pixelworld',
        '--model',args.model,'--thinking',args.reasoning,'--no-extensions','--no-mcp',
        '--no-skills','--no-prompt-templates','--no-context-files','--no-themes',
        '--session-dir',str(state/'native-sessions'),'--session-id',thread,
        '--no-builtin-tools','-e',str(Path(__file__).with_name('native_pi_extension.mjs'))]
    if args.track=='vision-agent'and(workspace/'first_frame.png').exists():
        command.append('@'+str(workspace/'first_frame.png'))
    task=sys.stdin.read()
    command.append(task)
    atomic_json(logs/'native_launch.json',{'backend':'pi','version':'1.6.0','model':args.model,
        'reasoning':args.reasoning,'tools':'native schemas with kernel-isolated operations','command':command})
    with (logs/'native_events.jsonl').open('wb') as raw:
        process=subprocess.Popen(command,cwd=workspace,env=environment,stdout=subprocess.PIPE)
        for line in iter(process.stdout.readline,b''):
            raw.write(line);raw.flush()
            try:projection.consume(json.loads(line))
            except (ValueError,KeyError) as error:
                projection.archive('normalization_error',{'error':str(error),'native_backend':'pi'})
        code=process.wait()
    for source in (state/'native-sessions').rglob('*.jsonl'):
        shutil.copy2(source,logs/'native_session.jsonl')
    event={'type':'turn.completed'if code==0 else'turn.failed'}
    if projection.requests:event['usage']=projection.totals
    if code!=0:event['error']={'message':'Native Pi exited '+str(code)}
    projection.emit(event);projection.session.close()
    return code


if __name__=='__main__':raise SystemExit(main())
