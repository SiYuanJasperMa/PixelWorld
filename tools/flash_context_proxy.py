"""An opt-in Responses relay for same-case, retrievable tool-history compaction."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import uuid
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

import requests
from pixelworld.context_replay import compact
from pixelworld.util import atomic_json

CORE_TOOLS={'exec_command','write_stdin','apply_patch','view_image'}


def core_tools(request, workspace):
    """Remove general UI/goal schemas while preserving all pixel-task tools."""
    tools=request.get('tools')
    if not isinstance(tools,list):return request,{}
    def name(tool):return tool.get('name') or tool.get('function',{}).get('name')
    retained=[tool for tool in tools if name(tool) in CORE_TOOLS]
    if not CORE_TOOLS <= {name(tool) for tool in retained}:
        raise ValueError('Core tool intervention requires the complete pixel-task tool set')
    directory=workspace/'.tmp/harness-history';directory.mkdir(parents=True,exist_ok=True)
    original=json.dumps(tools,ensure_ascii=False,separators=(',',':')).encode()
    digest=hashlib.sha256(original).hexdigest()
    path=directory/('tool-schemas-'+digest[:20]+'.json')
    if not path.exists():path.write_bytes(original)
    return {**request,'tools':retained},{
        'tool_schema_bytes_before':len(original),
        'tool_schema_bytes_after':len(json.dumps(retained,ensure_ascii=False,separators=(',',':')).encode()),
        'removed_tools':[name(tool) for tool in tools if name(tool) not in CORE_TOOLS]}


def workspace_for(token,registry,allowed_root):
    if not token or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid case token')
    records=json.loads(registry.read_text())
    if token not in records:raise ValueError('Unregistered case')
    workspace=Path(records[token]).resolve()
    if workspace.name!='workspace' or not workspace.is_relative_to(allowed_root.resolve()):
        raise ValueError('Case workspace is outside the registered experiment')
    return workspace


class Relay(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def log_message(self,*_):
        pass

    def error(self,status,text):
        raw=json.dumps({'error':{'message':text}}).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def route(self):
        parts=urlparse(self.path).path.strip('/').split('/')
        if len(parts)<4 or parts[0]!='case' or parts[2]!='v1':
            raise ValueError('Use the registered per-case Responses route')
        workspace=workspace_for(parts[1],self.server.registry,self.server.allowed_root)
        return parts[1],workspace,'/'.join(parts[3:])

    def forward(self,method,body=None):
        if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.local_key):
            return self.error(401,'Local proxy authentication required')
        headers_sent=False
        try:
            token,workspace,path=self.route()
            if path not in ('responses','models'):raise ValueError('Unsupported endpoint')
            boundary_mode = getattr(self.server,'stop_on_submission',False) and method=='POST' and path=='responses'
            ledger_path=None
            if boundary_mode:
                from pixelworld.submission_boundary import accept_boundary, STOP_MESSAGE
                digest=hashlib.sha256(body).hexdigest()
                if accept_boundary(workspace,digest):
                    return self.error(409,STOP_MESSAGE)
                ledger_path=workspace.parent/'logs/submission_requests'/(uuid.uuid4().hex+'.json')
                atomic_json(ledger_path,{'phase':'started','request_sha256':digest,'utc':time.time()})
            headers={'Authorization':self.headers.get('Authorization',''),'Content-Type':'application/json'}
            report=None
            if body is not None:
                request=json.loads(body)
                items=request.get('input')
                if isinstance(items,list) and not self.server.no_history_compaction:
                    new_items,report=compact(items,keep_recent=4,keep_generated_images=2,
                        archive_dir=workspace/'.tmp/harness-history',
                        retain_tool_calls=self.server.retain_tool_calls,
                        only_if_smaller=self.server.only_shrinking_returns)
                    request['input']=new_items
                if self.server.core_tools_only:
                    request,schemas=core_tools(request,workspace)
                    report={**(report or {}),**schemas}
                body=json.dumps(request,ensure_ascii=False,separators=(',',':')).encode()
            upstream=requests.request(method,self.server.upstream+'/'+path,data=body,
                headers=headers,stream=True,timeout=(20,3600))
            if report is not None:
                record={k:v for k,v in report.items() if k!='archived'}
                record.update(utc=time.time(),case_token=token,
                    forwarded_sha256=hashlib.sha256(body).hexdigest(),upstream_status=upstream.status_code)
                with self.server.log_lock:
                    with self.server.log_path.open('a') as out:out.write(json.dumps(record)+'\n')
                    archive=workspace/'.tmp/harness-history'
                    archive.mkdir(parents=True,exist_ok=True)
                    # Compact per-request evidence, not another copy of the
                    # large image history already logged by the upstream relay.
                    (archive/('request-'+record['forwarded_sha256'][:20]+'.json')).write_text(json.dumps(record))
            self.send_response(upstream.status_code)
            self.send_header('Content-Type',upstream.headers.get('Content-Type','application/json'))
            self.send_header('Connection','close');self.end_headers()
            headers_sent=True
            pending=b''
            try:
                while True:
                    chunk=upstream.raw.read1(8192)
                    if not chunk:break
                    self.wfile.write(chunk);self.wfile.flush()
                    if ledger_path is not None:
                        lines=(pending+chunk).split(b'\n');pending=lines.pop()
                        for line in lines:
                            if not line.startswith(b'data:'):continue
                            try:event=json.loads(line[5:].strip())
                            except ValueError:continue
                            if event.get('type')=='response.completed':
                                response=event.get('response')or{}
                                atomic_json(ledger_path,{'phase':'completed','request_sha256':digest,
                                    'utc':time.time(),'response_id':response.get('id'),
                                    'usage':response.get('usage')or{}})
            finally:
                upstream.close();self.close_connection=True
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True
        except Exception as error:
            if headers_sent:self.close_connection=True
            else:self.error(502,type(error).__name__+': '+str(error))

    def do_GET(self):
        if self.path=='/health':
            raw=b'{"ok":true,"variant":"retrievable-replay-v1"}'
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        else:self.forward('GET')

    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        if size>128*1024**2:return self.error(413,'Request exceeds the existing proxy size bound')
        self.forward('POST',self.rfile.read(size))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',type=Path,required=True)
    parser.add_argument('--allowed-root',type=Path,required=True)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--key-file',type=Path,required=True)
    parser.add_argument('--port',type=int,default=9588)
    parser.add_argument('--upstream',default='http://127.0.0.1:9587/v1')
    parser.add_argument('--retain-tool-calls',action='store_true')
    parser.add_argument('--no-history-compaction',action='store_true')
    parser.add_argument('--core-tools-only',action='store_true')
    parser.add_argument('--only-shrinking-returns',action='store_true')
    parser.add_argument('--stop-on-accepted-submission',action='store_true')
    args=parser.parse_args()
    if args.stop_on_accepted_submission and (not args.no_history_compaction or args.core_tools_only):
        parser.error('Submission boundary preserves the original history and all tools')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Relay)
    server.registry=args.registry;server.allowed_root=args.allowed_root
    server.log_path=args.log;server.log_path.parent.mkdir(parents=True,exist_ok=True)
    server.log_lock=threading.Lock();server.upstream=args.upstream.rstrip('/')
    server.local_key=args.key_file.read_text().strip()
    server.retain_tool_calls=args.retain_tool_calls
    server.no_history_compaction=args.no_history_compaction
    server.core_tools_only=args.core_tools_only
    server.only_shrinking_returns=args.only_shrinking_returns
    server.stop_on_submission=args.stop_on_accepted_submission
    server.serve_forever()
