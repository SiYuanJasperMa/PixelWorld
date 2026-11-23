"""Kernel-isolated operations for native agent tools; no model or evaluator API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def sandbox_command(workspace: Path, runtime: Path, argv: list[str]) -> list[str]:
    workspace = workspace.resolve()
    runtime = runtime.resolve()
    bwrap = runtime / 'bin/bwrap'
    if not bwrap.is_file():
        raise FileNotFoundError('The pinned runtime needs bwrap')
    # No /proc mount: tool processes cannot inspect the outer API credentials
    # or other agent processes, including on hosts restricting PID namespaces.
    command = [str(bwrap), '--unshare-user', '--unshare-net',
               '--die-with-parent', '--new-session', '--dev', '/dev']
    for root in ('/usr', '/bin', '/lib', '/lib64', '/etc'):
        if Path(root).exists():
            command += ['--ro-bind', root, root]
    for root in (runtime/'bin', runtime/'lib'):
        if root.exists():
            command += ['--ro-bind', str(root), str(root)]
    command += ['--bind', str(workspace), str(workspace), '--chdir', str(workspace), '--clearenv']
    environment = {'PATH':str(runtime/'bin')+':/usr/bin:/bin',
        'TMPDIR':str(workspace/'.tmp'), 'XDG_CACHE_HOME':str(workspace/'.tmp/cache'),
        'XDG_CONFIG_HOME':str(workspace/'.tmp/config'), 'MPLCONFIGDIR':str(workspace/'.tmp/matplotlib'),
        'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1', 'PYTHONDONTWRITEBYTECODE':'1'}
    for key,value in environment.items():
        command += ['--setenv',key,value]
    return command + argv


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('operation',choices=('read','head','access','mkdir','write','bash'))
    parser.add_argument('value')
    args=parser.parse_args()
    python=str(args.runtime.resolve()/'bin/python')
    statements={
        'read':'import sys;sys.stdout.buffer.write(open(sys.argv[1],"rb").read())',
        'head':'import sys;sys.stdout.buffer.write(open(sys.argv[1],"rb").read(32))',
        'access':'import os,sys;sys.exit(0 if os.access(sys.argv[1],os.R_OK) else 1)',
        'mkdir':'from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(parents=True,exist_ok=True)',
        'write':'import sys;data=sys.stdin.buffer.read();open(sys.argv[1],"wb").write(data)',
    }
    if args.operation=='bash':
        argv=['/bin/bash','--noprofile','--norc','-c',args.value]
    else:
        path=Path(args.value)
        if not path.is_absolute():path=args.workspace/path
        argv=[python,'-c',statements[args.operation],str(path)]
    command=sandbox_command(args.workspace,args.runtime,argv)
    # Never inherit provider keys or agent session locations into tool processes.
    environment={'PATH':'/usr/bin:/bin'}
    raise SystemExit(subprocess.call(command,env=environment))


if __name__=='__main__':main()
