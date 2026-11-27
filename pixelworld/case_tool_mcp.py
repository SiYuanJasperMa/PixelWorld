"""Case-only file, image and shell tools over a local MCP stdio transport.

The launcher chooses the case directory and pinned runtime. Requests cannot
select mounts or an alternate runtime. The model/API process is outside this
server's tool sandbox and its credentials are not inherited by tool children.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import os
import subprocess
import sys

from .native_sandbox import sandbox_command

MAX_TEXT = 128 * 1024
MAX_IMAGE = 12 * 1024 * 1024
_TOOL_SPECS = {
    'read': ('Read a UTF-8 file from this case', {'path':'string'}),
    'write': ('Write a UTF-8 file in this case; its parent must exist', {'path':'string','content':'string'}),
    'edit': ('Replace exactly one occurrence in a case file', {'path':'string','old':'string','new':'string'}),
    'bash': ('Run Bash in this case without network access', {'command':'string'}),
    'view_image': ('Return a PNG, JPEG, or WebP image from this case', {'path':'string'}),
}

_READ = 'import sys; f=open(sys.argv[1],"rb"); sys.stdout.buffer.write(f.read(int(sys.argv[2])+1))'
_WRITE = 'import sys; open(sys.argv[1],"wb").write(sys.stdin.buffer.read())'
_EDIT = '''import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
old, new = json.load(sys.stdin)
with path.open('rb') as f:
    raw = f.read(128 * 1024 + 1)
if len(raw) > 128 * 1024:
    raise ValueError('Text file exceeds edit limit')
body = raw.decode('utf-8')
if not old or body.count(old) != 1:
    raise ValueError('Edit must match exactly once')
path.write_bytes(body.replace(old, new, 1).encode('utf-8'))
'''


def tool_list() -> list[dict]:
    return [{'name':name, 'description':description, 'inputSchema':{
        'type':'object', 'properties':{key:{'type':kind} for key,kind in fields.items()},
        'required':list(fields), 'additionalProperties':False,
    }} for name,(description,fields) in _TOOL_SPECS.items()]


def _image_type(payload: bytes) -> str:
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if payload.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if payload.startswith(b'RIFF') and payload[8:12] == b'WEBP':
        return 'image/webp'
    raise ValueError('Unsupported case image')


def _text(message: str, *, error: bool = False) -> dict:
    return {'content':[{'type':'text','text':message}], 'isError':error}


class CaseTools:
    def __init__(self, workspace: Path, runtime: Path):
        self.workspace = workspace.resolve(strict=True)
        self.runtime = runtime.resolve(strict=True)
        if not self.workspace.is_dir() or not self.runtime.is_dir():
            raise ValueError('Expected case and runtime directories')
        self.python = str(self.runtime/'bin/python')

    def _path(self, value: str) -> str:
        p = Path(value)
        p = p if p.is_absolute() else self.workspace/p
        if not value or '\x00' in value or '..' in p.parts or not p.is_relative_to(self.workspace) or p == self.workspace:
            raise ValueError('Path must name a file inside the case')
        # Do not resolve candidate symlinks in the controller. The kernel
        # resolves them inside the case mount namespace during the operation.
        return str(p)

    def _execute(self, args, data=None):
        return subprocess.run(sandbox_command(self.workspace,self.runtime,args), input=data or b'',
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=int(os.environ.get('PIXELWORLD_CASE_TOOL_TIMEOUT','60')), env={'PATH':'/usr/bin:/bin'})

    def call(self, name: str, arguments: dict) -> dict:
        if name not in _TOOL_SPECS or not isinstance(arguments, dict):
            return _text('Unknown case tool',error=True)
        fields = _TOOL_SPECS[name][1]
        if set(arguments) != set(fields) or any(not isinstance(v,str) for v in arguments.values()):
            return _text('Invalid case tool arguments',error=True)
        if any(len(v.encode()) > MAX_TEXT for v in arguments.values()):
            return _text('Tool arguments exceed the size limit',error=True)
        try:
            if name == 'bash':
                command = arguments['command']
                # Bound terminal output without limiting legitimate output
                # artifacts (for example, large RGB tensors written by Python).
                args = ['/bin/bash','--noprofile','--norc','-c',
                        f'/bin/bash --noprofile --norc -c "$1" 2>&1 | head -c {MAX_TEXT}; exit ${{PIPESTATUS[0]}}',
                        '--', command]
                result = self._execute(args)
                log = (result.stdout+result.stderr)[:MAX_TEXT].decode(errors='replace')
                return _text(f'exit_code={result.returncode}\n{log}',error=result.returncode!=0)
            path = self._path(arguments['path'])
            if name in ('read','view_image'):
                limit = MAX_IMAGE if name=='view_image' else MAX_TEXT
                result = self._execute([self.python,'-c',_READ,path,str(limit)])
                if result.returncode != 0:
                    return _text('Case file read failed: '+result.stderr[:1024].decode(errors='replace'),error=True)
                if len(result.stdout)>limit:
                    return _text('Case file exceeds the tool size limit',error=True)
                if name=='view_image':
                    kind=_image_type(result.stdout)
                    return {'content':[{'type':'image','mimeType':kind,'data':base64.b64encode(result.stdout).decode()}],'isError':False}
                return _text(result.stdout.decode())
            if name=='write':
                result=self._execute([self.python,'-c',_WRITE,path],arguments['content'].encode())
            else:
                data=json.dumps([arguments['old'],arguments['new']]).encode()
                result=self._execute([self.python,'-c',_EDIT,path],data)
            if result.returncode!=0:
                return _text('Case file operation failed: '+result.stderr[:1024].decode(errors='replace'),error=True)
            return _text('OK')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return _text(str(exc)[:1024],error=True)


def handle(tools: CaseTools, request: dict) -> dict | None:
    if 'id' not in request:
        return None
    mid, method = request['id'], request.get('method')
    response = {'jsonrpc':'2.0','id':mid}
    params = request.get('params') or {}
    if method=='initialize':
        version=params.get('protocolVersion','2024-11-05')
        if version not in ('2024-11-05','2025-03-26','2025-06-18'):
            version='2025-06-18'
        result={'protocolVersion':version,'serverInfo':{'name':'pixelworld-case-tools','version':'1.0'},'capabilities':{'tools':{}}}
    elif method=='ping':result={}
    elif method=='tools/list':result={'tools':tool_list()}
    elif method=='tools/call':result=tools.call(params.get('name'),params.get('arguments',{}))
    else:return {**response,'error':{'code':-32601,'message':'Method not found'}}
    return {**response,'result':result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args();tools=CaseTools(args.workspace,args.runtime)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            if not isinstance(request,dict):raise ValueError('Expected a JSON-RPC object')
            result=handle(tools,request)
        except (ValueError,TypeError,AttributeError):
            result={'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}}
        if result is not None:
            print(json.dumps(result,separators=(',',':')),flush=True)

if __name__=='__main__':main()
