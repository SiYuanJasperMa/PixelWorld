"""Export verified remote evaluator process identities for the live dashboard."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

PROBE = """from pathlib import Path
import json,sys
root=sys.argv[1];rows=[]
for proc in Path('/proc').glob('[0-9]*'):
 try:
  if (proc/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':continue
  argv=[x.decode(errors='replace')for x in(proc/'cmdline').read_bytes().split(b'\\0')if x]
  if argv and root in ' '.join(argv) and any(token in ' '.join(argv)for token in ['evaluate_pcve_full.py','tools.evaluate_pcve_full','official_driver.py','evaluate_robojudge_shared50.py','run_gpu_evaluation.py','pcve_worker_start.py','tools.run_pcve_gpu_worker','watch_physics_evaluation.py','physiq.run_physics_iq']):rows.append([int(proc.name),argv])
 except OSError:pass
print(json.dumps(rows))
"""


def check(worker, root):
    command = ['ssh','-p','22222','-o','BatchMode=yes','-o','ConnectTimeout=10',worker,
               'python3 -c '+shlex.quote(PROBE)+' '+shlex.quote(str(root))]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=25, check=True)
        return {'worker':worker,'checked_unix':time.time(),'commands':json.loads(result.stdout),'status':'verified'}
    except (subprocess.SubprocessError, ValueError) as error:
        return {'worker':worker,'checked_unix':time.time(),'commands':[],'status':'unknown','error_type':type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--worker', action='append', required=True)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    root = args.run_root.resolve()
    output = root/'state/gpu_evaluator_worker_health.json'
    while True:
        with ThreadPoolExecutor(max_workers=len(args.worker)) as pool:
            workers=list(pool.map(lambda worker:check(worker,root),args.worker))
        payload={'checked_unix':time.time(),'workers':workers,
                 'commands':[row for worker in workers for row in worker['commands']]}
        temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(payload,indent=2));temporary.replace(output)
        print({'workers':len(workers),'verified':sum(w['status']=='verified'for w in workers),
               'processes':len(payload['commands'])},flush=True)
        if args.once:return
        time.sleep(args.interval)


if __name__=='__main__':main()
