"""Probe actual native Codex case isolation without using a model or secrets."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from pixelworld.permissions import case_permissions, permission_args


def verify(root: Path, codex: str):
    workspace = root/'workspace'; sub = workspace/'subdir'
    sub.mkdir(parents=True, exist_ok=True)
    (workspace/'inside.txt').write_text('inside-ok')
    outside = root/'outside-canary.txt'; outside.write_text('synthetic-test-canary')
    link = workspace/'outside-link'; link.unlink(missing_ok=True); link.symlink_to(outside)
    (root/'codex-home').mkdir(exist_ok=True)
    profile = case_permissions(workspace)
    (root/'profile.json').write_text(json.dumps(profile, indent=2))
    body = """import json, socket
from pathlib import Path
checks={}
checks['internal_relative_read']=Path('../inside.txt').read_text()=='inside-ok'
for name,path in %r.items():
 try:
  Path(path).read_text();checks[name]={'blocked':False}
 except OSError as e:checks[name]={'blocked':True,'errno':e.errno}
try:
 visible=sorted(p.name for p in Path(%r).iterdir());checks['external_names_hidden']={'blocked':'outside-canary.txt' not in visible,'visible_entries':visible}
except OSError as e:checks['external_names_hidden']={'blocked':True,'errno':e.errno}
sock=None
try:
 sock=socket.socket();sock.settimeout(2);sock.connect(('127.0.0.1',9));checks['network']={'blocked':False}
except OSError as e:checks['network']={'blocked':e.errno in (1,13),'errno':e.errno}
finally:
 if sock is not None:sock.close()
print(json.dumps(checks))
""" % ({'absolute_external_read':str(outside), 'relative_external_read':'../../outside-canary.txt',
           'symlink_external_read':'../outside-link'},str(root))
    env=os.environ.copy();env['CODEX_HOME']=str(root/'codex-home');env['TMPDIR']=str(workspace)
    command=[codex,'sandbox',*permission_args(profile),'-P',profile['name'],'-C',str(sub),sys.executable,'-c',body]
    result=subprocess.run(command,env=env,text=True,capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    checks=json.loads(result.stdout.strip().splitlines()[-1])
    passed=checks['internal_relative_read'] and all(x['blocked']for k,x in checks.items()if k!='internal_relative_read')
    report={'pass':passed,'codex_version':subprocess.check_output([codex,'--version'],text=True).strip(),
            'profile':'case-only-v1','checks':checks,'scope':'synthetic native sandbox filesystem and direct-network probes; no agent API calls'}
    (root/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--codex',required=True)
    a=p.parse_args();result=verify(a.root.resolve(),a.codex);print(json.dumps(result));sys.exit(0 if result['pass'] else 1)
