"""Local experimental Responses -> TRAE raw-chat bridge. No CLI agent upstream."""
import argparse, json, os, secrets, threading, time, uuid, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
from pixelworld.provider_completion import DEFAULT_PROVIDER_MAX_OUTPUT_TOKENS, native_output_limit, terminal_status
from pixelworld.log_storage import add_storage_arguments, resolve_storage_arguments
MODEL_CONFIG = {}
MODELS = {}

def flatten_tools(tools, namespace=None):
    for t in tools:
        if t.get('type') == 'namespace':
            yield from flatten_tools(t.get('tools', []), t['name'])
        else:
            value = dict(t)
            if namespace:
                value['_namespace'] = namespace
                value['_original_name'] = t['name']
                value['name'] = namespace + '__' + t['name']
            yield value

def content_parts(value):
    if isinstance(value, str):
        return [{'type': 'text', 'text': value}]
    parts = []
    for p in value or []:
        if p.get('type') in ('input_text', 'output_text', 'text'):
            parts.append({'type': 'text', 'text': p.get('text', '')})
        elif p.get('type') in ('input_image', 'image_url'):
            url = p.get('image_url')
            if isinstance(url, str): url = {'url': url}
            parts.append({'type': 'image_url', 'image_url': url})
        else:
            raise ValueError('Unsupported content type: ' + str(p.get('type')))
    return parts

def native_request(req):
    model = req.get('model')
    if isinstance(model,str) and model.startswith('traex/'):model=model[len('traex/'):]
    if model not in MODELS: raise ValueError('Model not in current TRAE catalog')
    if req.get('previous_response_id'): raise ValueError('Use stateless full input, not previous_response_id')
    messages = []
    if req.get('instructions'):
        messages.append({'role': 'system', 'content': content_parts(req['instructions'])})
    items = req.get('input', [])
    if isinstance(items, str): items = [{'role':'user','content':items}]
    for item in items:
        typ = item.get('type','message')
        if typ == 'message':
            role = item.get('role','user')
            if role == 'developer': role = 'system'
            messages.append({'role':role,'content':content_parts(item.get('content',[]))})
        elif typ in ('function_call','custom_tool_call'):
            kind = 'custom' if typ == 'custom_tool_call' else 'function'
            name = item['name']
            if item.get('namespace'): name = item['namespace'] + '__' + name
            data = {'name': name, 'arguments':
                    item.get('input' if kind=='custom' else 'arguments','')}
            if kind=='custom':
                data['arguments']=json.dumps({'input':item.get('input','')},ensure_ascii=False)
            # Parallel Responses calls belong to one assistant tool batch.
            # Keep their IDs and ordered results, rather than inserting another
            # assistant turn before the first batch's tool results arrive.
            if not (messages and messages[-1].get('role')=='assistant'
                    and not messages[-1].get('content') and messages[-1].get('tool_calls')):
                messages.append({'role':'assistant','content':[], 'tool_calls':[]})
            batch=messages[-1]['tool_calls']
            batch.append({'index':len(batch),'id':item['call_id'],'type':'function','function_call':data})
        elif typ in ('function_call_output','custom_tool_call_output'):
            output = item.get('output','')
            if isinstance(output,list):
                parts = content_parts(output)
                messages.append({'role':'tool','tool_call_id':item['call_id'],'content':parts})
            else:
                messages.append({'role':'tool','tool_call_id':item['call_id'],
                                 'content':content_parts(output if isinstance(output,str) else json.dumps(output))})
        elif typ == 'reasoning':
            summary = '\n'.join(p.get('text','') for p in item.get('summary',[]))
            if summary: messages.append({'role':'assistant','content':[], 'reasoning_content':summary})
        else:
            raise ValueError('Unsupported input item: '+str(typ))
    tools=[]
    for t in flatten_tools(req.get('tools',[])):
        typ=t.get('type')
        if typ == 'function':
            fn={k:t[k] for k in ('name','description','parameters','strict') if k in t}
            if isinstance(fn.get('parameters'),dict):
                fn['parameters']=json.dumps(fn['parameters'],ensure_ascii=False,separators=(',',':'))
            tools.append({'type':'function','function':fn})
        elif typ == 'custom':
            description=t.get('description','')+'\nPass the complete original freeform tool input in the input argument.'
            fmt=t.get('format',{})
            if fmt.get('type')=='grammar':description+='\nOriginal input grammar:\n'+fmt.get('definition','')
            schema={'type':'object','properties':{'input':{'type':'string'}},'required':['input'],'additionalProperties':False}
            tools.append({'type':'function','function':{'name':t['name'],'description':description,
                          'parameters':json.dumps(schema,separators=(',',':'))}})
        else:
            raise ValueError('Unsupported tool type: '+str(typ))
    last_user = next((m for m in reversed(messages) if m['role']=='user'),None)
    user_input = '\n'.join(p.get('text','') for p in last_user['content'] if p.get('type')=='text') if last_user else ''
    return {'extra_info':json.dumps({'tool_wire_contract':'preserve_v1'}),'config_name':MODELS[model],
            'model_name':MODEL_CONFIG[model]['native_model_name'],'user_input':user_input,'messages':messages,
            'session_id':(req.get('prompt_cache_key') or str(uuid.uuid4())),'conversation_id':(req.get('prompt_cache_key') or str(uuid.uuid4())),'is_preset':True,
            'tools':tools,'max_tokens':native_output_limit(req),
            'reasoning_effort':req.get('reasoning',{}).get('effort',MODEL_CONFIG[model].get('default_reasoning_level')),
            'parallel_tool_calls':req.get('parallel_tool_calls',True),'access_type':4}

def sse_events(response):
    response.encoding='utf-8'
    event, data = None, []
    for line in response.iter_lines(decode_unicode=True):
        if line == '':
            if data: yield event,'\n'.join(data)
            event,data=None,[]
        elif line.startswith('event:'):event=line[6:].strip()
        elif line.startswith('data:'):data.append(line[5:].lstrip())
    if data:yield event,'\n'.join(data)

def queue_aware_events(initial, reopen, wait, retry_delay):
    """Retry explicit pre-output queue/rate rejection, preserving one request."""
    current=initial;attempt=1
    while True:
        sampled=False;rejected=None
        try:
            if current.status_code==429 and retry_delay>0:
                rejected={'http_status':429,'message':'Upstream rate limit'}
                yield 'queue_wait',json.dumps({'attempt':attempt,'retry_delay_seconds':retry_delay,'native_error':rejected,'model_output_observed':False})
            elif current.status_code!=200:
                yield 'error',json.dumps({'http_status':current.status_code,'message':'Upstream HTTP failure'})
                return
            for event,data in ([] if rejected else sse_events(current)):
                try:value=json.loads(data)
                except ValueError:value=data
                if event=='output' and isinstance(value,dict) and any(value.get(k) for k in ('response','reasoning_content','tool_calls','multimodal_contents')):
                    sampled=True
                if event=='token_usage' and isinstance(value,dict) and any(value.get(k,0) for k in ('prompt_tokens','completion_tokens','reasoning_tokens')):
                    sampled=True
                if event=='error' and isinstance(value,dict) and value.get('code') in (4050,4055) and not sampled and retry_delay>0:
                    rejected=value
                    yield 'queue_wait',json.dumps({'attempt':attempt,'retry_delay_seconds':retry_delay,'native_error':value,'model_output_observed':False})
                    break
                yield event,data
        finally:current.close()
        if rejected is None:return
        wait(retry_delay)
        current=reopen();attempt+=1

class Bridge(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def json_reply(self,status,value):
        data=json.dumps(value).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def authorized(self):
        return secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.local_key)
    def do_GET(self):
        if self.path in ('/health','/ready'):
            budget=getattr(self.server,'spool_budget',None)
            storage=None;ready=True
            if budget is not None:
                try:
                    storage=budget.capacity_status(self.server.max_request_bytes)
                    ready=storage['ready']
                except OSError:
                    storage={'ready':False,'error':'storage_status_unavailable'};ready=False
            # Liveness alone must not imply that a model request can be admitted.
            return self.json_reply(200 if self.path=='/health' or ready else 503,
                {'ok':True if self.path=='/health' else ready,'adapter':'trae-raw-chat-v1',
                 'ready':ready,'storage':storage})
        if not self.authorized():return self.json_reply(401,{'error':{'message':'Local proxy authentication required'}})
        if self.path in ('/v1/models','/models'):
            return self.json_reply(200,{'object':'list','data':[{'id':'traex/'+m,'object':'model','owned_by':'trae-custom-proxy',
                 'display_name':'[TRAE 代理·自配] '+m,'aliases':[m],
                 'input_modalities':MODEL_CONFIG[m]['input_modalities']} for m in MODELS]})
        self.json_reply(404,{'error':{'message':'Unknown path'}})
    def do_POST(self):
        if not self.authorized():return self.json_reply(401,{'error':{'message':'Local proxy authentication required'}})
        if self.path not in ('/v1/responses','/responses'):
            return self.json_reply(404,{'error':{'message':'Unknown path'}})
        rid='resp_'+uuid.uuid4().hex
        record={'id':rid,'started':time.time()}
        stream_started=False;emit=None;native_events=[];upstream=None;s=None;auth='';usage=None
        budget=getattr(self.server,'spool_budget',None);leased=False
        def save_log(suffix, value, raw=False):
            writer=getattr(self.server,'cloud_log_writer',None)
            if writer is not None and suffix!='active.json':
                writer.submit(rid+'.'+suffix,value,raw=raw)
                return
            data=(value if raw else json.dumps(value,ensure_ascii=False)).encode()
            if budget:budget.write(rid,rid+'.'+suffix,data)
            else:
                p=self.server.log_dir/(rid+'.'+suffix);p.write_bytes(data);p.chmod(0o600)
        try:
            size=int(self.headers.get('Content-Length',0))
            if size>self.server.max_request_bytes:
                raise ValueError('Request exceeds local size limit')
            if budget:
                budget.acquire(rid,size);leased=True
            save_log('active.json',{'started':record['started']})
            req=json.loads(self.rfile.read(size))
            streaming=bool(req.get('stream',False))
            save_log('input.json',req)
            native_input=dict(req)
            native_input['max_output_tokens']=native_output_limit(req,getattr(self.server,'default_max_output_tokens',DEFAULT_PROVIDER_MAX_OUTPUT_TOKENS))
            payload=native_request(native_input)
            if getattr(self.server,'use_request_parameters',False):
                payload['is_preset']=False
            if getattr(self.server,'lossless_input_images',False):
                from pixelworld.traex_image_transport import lossless_payload
                payload=lossless_payload(payload)
            credential=json.loads(self.server.auth_file.read_text())['trae']
            auth=credential['access_token']
            if credential.get('credential_kind')!='cloud_cli_jwt':
                raise ValueError('This smoke currently supports only verified cloud_cli_jwt credentials')
            headers={'Authorization':'Cloud-CLI-JWT '+auth,
                     'Content-Type':'application/json','Accept':'text/event-stream',
                     'x-app-id':'6eefa01c-1036-4c7e-9ca5-d891f63bfcd8',
                     'x-ide-function':'traecli_next','x-ide-version-code':'20261108',
                     'version':'0.214.1','x-agent-flag':'1','originator':'codex_exec',
                     'User-Agent':self.headers.get('User-Agent','codex_exec/0.167.0')}
            record.update(model=req['model'],native_model=payload['model_name'],
                          thread_id=req.get('prompt_cache_key'),
                          input_items=len(req.get('input',[])),tool_names=[t.get('name') for t in req.get('tools',[])])
            save_log('native.json',payload)
            s=requests.Session();s.trust_env=False
            def reopen():
                return s.post(self.server.upstream,headers=headers,json=payload,stream=True,timeout=(10,self.server.upstream_read_timeout))
            upstream=reopen()
            record['http_status']=upstream.status_code
            if upstream.status_code!=200:
                text=upstream.text
                save_log('error.txt',text,raw=True)
                record['upstream_error_preview']=text[:500].replace(auth,'[redacted]')
                return self.json_reply(upstream.status_code,{'error':{'message':'Upstream rejected request; inspect local redacted diagnostics'}})
            if streaming:
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Cache-Control','no-cache');self.send_header('Connection','close');self.end_headers()
                stream_started=True
            seq=0;outputs=[];text_item=None;tool_items={};custom_arguments={};usage=None;finished=False
            def emit(typ,**kwargs):
                nonlocal seq
                obj={'type':typ,'sequence_number':seq,**kwargs};seq+=1
                if streaming:
                    self.wfile.write(('event: '+typ+'\ndata: '+json.dumps(obj,ensure_ascii=False)+'\n\n').encode());self.wfile.flush()
            def response(status):
                return {'id':rid,'object':'response','created_at':int(record['started']),
                        'status':status,'model':req['model'],'output':outputs,'usage':usage,
                        'error':None,'incomplete_details':terminal_status(record.get('finish_reason'))[1] if status=='incomplete' else None}
            emit('response.created',response=response('in_progress'))
            emit('response.in_progress',response=response('in_progress'))
            def ensure_text(phase=None):
                nonlocal text_item
                if text_item is None:
                    text_item={'id':'msg_'+uuid.uuid4().hex,'type':'message','status':'in_progress',
                               'role':'assistant','content':[]}
                    if phase in ('commentary','final_answer'):text_item['phase']=phase
                    idx=len(outputs);outputs.append(text_item)
                    emit('response.output_item.added',output_index=idx,item=dict(text_item))
                    text_item['content'].append({'type':'output_text','text':'','annotations':[]})
                    emit('response.content_part.added',item_id=text_item['id'],output_index=idx,
                         content_index=0,part={'type':'output_text','text':'','annotations':[]})
                return outputs.index(text_item)
            native_events=[]
            def queue_wait(seconds):
                until=time.monotonic()+seconds
                while time.monotonic()<until:
                    if streaming:
                        self.wfile.write(b': waiting for upstream capacity\n\n');self.wfile.flush()
                    time.sleep(min(10,max(0,until-time.monotonic())))
            for event,data in queue_aware_events(upstream,reopen,queue_wait,self.server.queue_retry_delay):
                if streaming:
                    self.wfile.write(b': upstream activity\n\n');self.wfile.flush()
                try:v=json.loads(data)
                except json.JSONDecodeError:v=data
                native_events.append({'event':event,'data':v})
                if event=='queue_wait':
                    record.setdefault('queue_rejections',[]).append(v)
                    state={'id':rid,'model':record.get('model'),'state':'waiting_for_upstream_capacity',
                           'thread_id':req.get('prompt_cache_key'),'updated_at':time.time(),'rejections':len(record['queue_rejections'])}
                    save_log('active.json',state)
                elif event=='metadata':record['metadata']=v
                elif event=='timing_cost':record['timing']=v
                elif event=='token_usage':
                    record['native_usage']=v
                    usage={'input_tokens':v.get('prompt_tokens',0),'output_tokens':v.get('completion_tokens',0),
                           'total_tokens':v.get('total_tokens',0),
                           'input_tokens_details':{'cached_tokens':v.get('cache_read_input_tokens',0)},
                           'output_tokens_details':{'reasoning_tokens':v.get('reasoning_tokens',0)}}
                elif event=='output' and isinstance(v,dict):
                    delta=v.get('response')
                    if delta:
                        idx=ensure_text(v.get('phase'));text_item['content'][0]['text']+=delta
                        emit('response.output_text.delta',item_id=text_item['id'],output_index=idx,content_index=0,delta=delta)
                    calls=v.get('tool_calls') or []
                    if isinstance(calls,str):calls=json.loads(calls)
                    for pos,c in enumerate(calls):
                        native_idx=c.get('index',pos)
                        kind='custom' if c.get('type')=='custom' or 'custom' in c else 'function'
                        value=c.get('function_call',c.get('custom_call',c.get(kind,{}))) or {}
                        if native_idx not in tool_items:
                            name=value.get('name',c.get('name',''))
                            original=next((t for t in flatten_tools(req.get('tools',[])) if t.get('name')==name),None)
                            if original and original.get('type')=='custom':kind='custom'
                            item={'id':'tc_'+uuid.uuid4().hex,'type':'custom_tool_call' if kind=='custom' else 'function_call',
                                  'status':'in_progress','call_id':c.get('id') or 'call_'+uuid.uuid4().hex,
                                  'name':original.get('_original_name',name) if original else name,
                                  'input' if kind=='custom' else 'arguments':''}
                            if original and original.get('_namespace'):item['namespace']=original['_namespace']
                            idx=len(outputs);outputs.append(item);tool_items[native_idx]=(idx,item,kind)
                            emit('response.output_item.added',output_index=idx,item=dict(item))
                        idx,item,kind=tool_items[native_idx]
                        arg=value.get('input',value.get('arguments','')) or c.get('input','')
                        if arg:
                            if kind=='custom':custom_arguments[item['id']]=custom_arguments.get(item['id'],'')+arg
                            else:
                                item['arguments']+=arg
                                emit('response.function_call_arguments.delta',item_id=item['id'],output_index=idx,delta=arg)
                elif event=='done':
                    record['finish_reason']=v.get('finish_reason') if isinstance(v,dict) else v;finished=True
                elif event in ('error','exception'):
                    record['native_error']=v
                    raise ValueError('Upstream error: '+json.dumps(v,ensure_ascii=False)[:1000])
            if not finished:raise ValueError('Upstream stream ended without a done event')
            completion_status,_=terminal_status(record.get('finish_reason'))
            for idx,item,kind in tool_items.values():
                if completion_status!='completed':
                    if kind=='custom':item['input']=custom_arguments.get(item['id'],'')
                    continue
                if kind=='custom':
                    decoded=json.loads(custom_arguments.get(item['id'],'{}'))
                    if not isinstance(decoded.get('input'),str):raise ValueError('Wrapped custom tool did not return a string input')
                    item['input']=decoded['input']
                    emit('response.custom_tool_call_input.delta',item_id=item['id'],output_index=idx,delta=item['input'])
            for idx,item in enumerate(outputs):
                item['status']=completion_status
                if item['type']=='message':
                    emit('response.output_text.done',item_id=item['id'],output_index=idx,content_index=0,text=item['content'][0]['text'])
                    emit('response.content_part.done',item_id=item['id'],output_index=idx,content_index=0,part=item['content'][0])
                elif completion_status=='completed':
                    kind='custom' if item['type']=='custom_tool_call' else 'function'
                    key='input' if kind=='custom' else 'arguments'
                    emit('response.custom_tool_call_input.done' if kind=='custom' else 'response.function_call_arguments.done',
                         item_id=item['id'],output_index=idx,**{key:item[key]})
                emit('response.output_item.done',output_index=idx,item=item)
            emit('response.'+completion_status,response=response(completion_status))
            if not streaming:self.json_reply(200,response(completion_status))
            record.update(elapsed=time.time()-record['started'],usage=usage,output_types=[i['type'] for i in outputs])
            record['response_status']=completion_status
            for suffix,value in [('events.json',native_events),('response.json',response(completion_status))]:
                save_log(suffix,value)
            upstream.close();s.close()
        except (BrokenPipeError,ConnectionResetError):record['client_disconnected']=True
        except Exception as e:
            record['error_type']=type(e).__name__;record['error_message']=str(e)[:250]
            try:
                if stream_started and emit:
                    emit('response.failed',response={'id':rid,'object':'response','created_at':int(record['started']),
                         'status':'failed','model':record.get('model'),'output':[],
                         'usage':usage,'error':{'code':'upstream_error','message':str(e)[:1000]},'incomplete_details':None})
                else:self.json_reply(503 if str(e).startswith('storage_backpressure:') else 502,{'error':{'message':str(e)[:1000]}})
            except Exception:pass
        finally:
            if upstream is not None:upstream.close()
            if s is not None:s.close()
            record['elapsed']=time.time()-record['started']
            try:
                if native_events:save_log('events.json',native_events)
                writer=getattr(self.server,'cloud_log_writer',None)
                if writer is not None:
                    writer.submit('requests.jsonl',record,append=True)
                elif budget:
                    if leased:budget.append_summary(rid,record)
                else:
                    with self.server.lock:
                        with (self.server.log_dir/'requests.jsonl').open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
            finally:
                writer=getattr(self.server,'cloud_log_writer',None)
                if writer is not None:
                    writer.remove_marker(rid+'.active.json')
                else:
                    (self.server.log_dir/(rid+'.active.json')).unlink(missing_ok=True)
                if leased:budget.release(rid)
            print(json.dumps(record,ensure_ascii=False),flush=True)
            self.close_connection=True

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=9584)
    p.add_argument('--model-catalog',type=Path,required=True)
    p.add_argument('--lossless-input-images',action='store_true')
    p.add_argument('--use-request-parameters',action='store_true',
                   help='Request native model parameters instead of a server preset; verify effective settings before a matrix')
    p.add_argument('--auth-file',type=Path,default=Path.home()/'.trae/cli/auth.json')
    p.add_argument('--upstream',default='https://copilot-cn.bytedance.net/api/ide/v2/llm_raw_chat')
    p.add_argument('--log-dir',type=Path,default=Path.cwd()/'runs/traex_proxy_logs')
    p.add_argument('--upstream-read-timeout',type=float,default=180)
    p.add_argument('--queue-retry-delay',type=float,default=0)
    p.add_argument('--max-request-bytes',type=int,default=30_000_000)
    p.add_argument('--default-max-output-tokens',type=int,default=DEFAULT_PROVIDER_MAX_OUTPUT_TOKENS)
    add_storage_arguments(p)
    args=resolve_storage_arguments(p.parse_args(),p)
    global MODEL_CONFIG, MODELS
    MODEL_CONFIG={m['slug']:m for m in json.loads(args.model_catalog.read_text())['models']}
    MODELS={name:m['config_name'] for name,m in MODEL_CONFIG.items()}
    args.log_dir.mkdir(parents=True,exist_ok=True);args.log_dir.chmod(0o700)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Bridge)
    server.auth_file=args.auth_file;server.upstream=args.upstream;server.log_dir=args.log_dir
    server.upstream_read_timeout=args.upstream_read_timeout
    server.lossless_input_images=args.lossless_input_images
    server.use_request_parameters=args.use_request_parameters
    if args.max_request_bytes < 1:raise ValueError('--max-request-bytes must be positive')
    server.max_request_bytes=args.max_request_bytes
    server.default_max_output_tokens=native_output_limit({},args.default_max_output_tokens)
    server.queue_retry_delay=args.queue_retry_delay
    server.spool_budget=None
    server.cloud_log_writer=None
    if args.async_cloud_logs:
        from pixelworld.cloud_log_writer import CloudLogWriter
        server.cloud_log_writer=CloudLogWriter(args.log_dir)
    if args.spool_max_bytes:
        from pixelworld.log_storage import SpoolBudget
        server.spool_budget=SpoolBudget(args.log_dir,args.spool_max_bytes,args.min_free_bytes)
    # Keep loopback credentials stable across a daemon restart so detached
    # benchmark workers do not inherit a key that immediately becomes invalid.
    key_file=args.log_dir/'local.key'
    existing_key=key_file.read_text().strip() if key_file.exists() else ''
    server.local_key=existing_key or secrets.token_urlsafe(32);server.lock=threading.Lock()
    key_file.write_text(server.local_key);key_file.chmod(0o600)
    print('READY http://127.0.0.1:%d/v1'%server.server_port,flush=True)
    server.serve_forever()

if __name__=='__main__':main()
