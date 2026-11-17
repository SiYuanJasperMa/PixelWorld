"""Verify remote evaluator process identities for live dashboard state."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

from pixelworld.util import atomic_json


def probe(worker,port,root):
    script='''from pathlib import Path
import json,sys
root=Path(sys.argv[1]);commands=[];seen=set()
def visit(pid):
 if pid in seen:return
 seen.add(pid);proc=Path('/proc')/str(pid)
 try:
  if proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':return
  argv=[x.decode(errors='replace')for x in proc.joinpath('cmdline').read_bytes().split(b'\\0')if x]
  if any(str(root) in x for x in argv) and any('watch_physics_evaluation' in x or 'physiq.run_physics_iq' in x or 'evaluate_flash_subset' in x for x in argv):commands.append([pid,argv])
  for file in proc.joinpath('task').glob('*/children'):
   for child in file.read_text().split():visit(int(child))
 except (OSError,ValueError):return
for name in ['main_physics_evaluation.pid','flash_native24_evaluation.pid']:
 file=root/'state'/name
 if file.exists():
  pid=int(file.read_text());proc=Path('/proc')/str(pid)
  try:
   argv=proc.joinpath('cmdline').read_bytes()
   if b'watch_physics_evaluation' in argv or b'evaluate_flash_subset' in argv:visit(pid)
  except OSError:pass
print(json.dumps({'commands':commands}))
'''
    command='python3 -c '+shlex.quote(script)+' '+shlex.quote(str(root.resolve()))
    result=subprocess.run(['ssh','-p',str(port),'-o','BatchMode=yes','-o','ConnectTimeout=10',worker,command],
                          capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('Worker process probe failed: '+result.stderr[-400:])
    return json.loads(result.stdout)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',required=True)
    parser.add_argument('--port',type=int,default=22222)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--interval',type=float,default=60)
    args=parser.parse_args();path=args.run_root/'state/evaluator_worker_health.json'
    while True:
        try:
            report=probe(args.worker,args.port,args.run_root)
            report.update(checked_unix=time.time(),worker=args.worker)
            atomic_json(path,report);print('Verified remote evaluator processes',len(report['commands']),flush=True)
        except Exception as error:
            # A transient probe error is not evidence that a process stopped.
            print(type(error).__name__,str(error),flush=True)
        time.sleep(args.interval)
