"""Private, verified closed-run archives; no automatic deletion of source artifacts."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile

from .util import atomic_json, utc_now

SAFE = re.compile(r'[A-Za-z0-9_.-]+$')
RID = re.compile(r'resp_[a-zA-Z0-9]+$')
WIRE_ROLES = ('input.json','native.json','events.json','response.json','error.txt')
RUN_DIRECTORIES = ('input_snapshot', 'logs', 'trace', 'recording')
RUN_FILES = ('run_manifest.json', 'task_prompt.txt', 'media_contract.json', 'permissions_profile.json')
OUTPUT_FILES = ('generate.py', 'pixels.npz', 'inspection.png','transition_check.png','index.html','video.mp4')


def _safe(v):
    if not isinstance(v,str) or not SAFE.fullmatch(v) or v in ('.','..'):
        raise ValueError('Unsafe archive scope component')
    return v


def _closed_files(run):
    for name in RUN_FILES + tuple('workspace/'+x for x in OUTPUT_FILES):
        p=run/name
        if p.is_file() and not p.is_symlink():yield name,p
    for name in RUN_DIRECTORIES + ('workspace/frames',):
        loc=run/name
        if not loc.is_dir() or loc.is_symlink():continue
        for d, dirs, files in os.walk(loc,followlinks=False):
            dirs.sort()
            for fn in sorted(files):
                p=Path(d)/fn
                if p.is_file() and not p.is_symlink():yield p.relative_to(run).as_posix(),p


def _wire_files(run, log_root):
    ids=[]; ledger=run/'recording/api_usage.jsonl'
    if ledger.is_file():
        with ledger.open() as f:
            for line in f:
                x=json.loads(line);rid=x.get('response_id')
                if isinstance(rid,str) and RID.fullmatch(rid) and rid not in ids:ids.append(rid)
    files=[]; roles={}
    for rid in ids:
        roles[rid]=[]
        if log_root is None:continue
        for suffix in WIRE_ROLES:
            p=log_root/f'{rid}.{suffix}'
            if p.is_file() and not p.is_symlink() and p.resolve()==p:
                files.append((f'api_originals/{rid}/{suffix}',p));roles[rid].append(suffix)
    return files,roles


def _verify_tar(path, expected):
    seen=set()
    with tarfile.open(path,'r|gz')as tar:
        for member in tar:
            if member.name not in expected or not member.isfile() or member.name in seen:
                raise ValueError(f'Unexpected archive member: {member.name}')
            with tar.extractfile(member) as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=expected[member.name]:
                    raise ValueError(f'Archive readback digest mismatch: {member.name}')
            seen.add(member.name)
    if seen!=set(expected):raise ValueError('Missing archived members')


def archive_closed_attempt(run:Path, root:Path, *, benchmark:str, proxy_logs:Path|None=None) -> dict:
    """Pin an attempt in a numbered per-model directory, then independently read back payloads."""
    run,root=run.resolve(),root.absolute()
    if root.resolve()!=root or root.is_relative_to(run) or run.is_relative_to(root):
        raise ValueError('Archive must be a separate real directory')
    m=json.loads((run/'run_manifest.json').read_text())
    if m.get('status') not in ('complete','failed'):
        raise ValueError('Do not archive an active attempt as complete')
    if proxy_logs is not None:
        proxy_logs=proxy_logs.resolve(strict=True)
        if not proxy_logs.is_dir():raise ValueError('Proxy log root must be a directory')
    parent=root/_safe(benchmark)/_safe(m['model_key']);parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if parent.resolve()!=parent:raise ValueError('Archive hierarchy contains a symlink')
    case,track,attempt=_safe(str(m['case_id'])),_safe(m['track']),int(m['attempt'])
    key=f'{track}/{case}/{attempt}'; index=parent/'archive_index.json'
    with (parent/'.archive_lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=json.loads(index.read_text()) if index.is_file() else {}
        if key not in rows:
            rows[key]=max(rows.values(),default=0)+1;atomic_json(index,rows)
        num=rows[key];dest=parent/f'run_{num:04d}'
        dest.mkdir(mode=0o700,exist_ok=True)
        if dest.resolve()!=dest:raise ValueError('Archive destination contains a symlink')
    with (dest/'.archive_lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        receipt=dest/'verified.json';target=dest/'artifacts.tar.gz';tmp=dest/'artifacts.part'
        if receipt.is_file():
            old=json.loads(receipt.read_text())
            with (run/'run_manifest.json').open('rb')as f:current=hashlib.file_digest(f,'sha256').hexdigest()
            if (old['source_run'],old['members']['run_manifest.json'],old['archive_bytes'])!=(str(run),current,target.stat().st_size):
                raise ValueError('Existing verified archive represents a different source revision')
            return old
        files=list(_closed_files(run));wire_files,roles=_wire_files(run,proxy_logs);files.extend(wire_files)
        expected={}
        try:
            with tarfile.open(tmp,'w:gz',compresslevel=1)as tar:
                for name,src in files:
                    if src.resolve()!=src or name in expected:raise ValueError('Untrusted source member')
                    with os.fdopen(os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb')as handle:
                        before=os.fstat(handle.fileno())
                        if not stat.S_ISREG(before.st_mode):raise ValueError('Only regular files may be archived')
                        digest=hashlib.sha256()
                        class Reader:
                            def read(self,n):
                                b=handle.read(n);digest.update(b);return b
                        entry=tarfile.TarInfo(name);entry.mode=0o600;entry.size=before.st_size
                        tar.addfile(entry,Reader());after=os.fstat(handle.fileno())
                        if (before.st_ino,before.st_mtime_ns,before.st_size)!=(after.st_ino,after.st_mtime_ns,after.st_size):
                            raise ValueError(f'Source changed during archival: {name}')
                        expected[name]=digest.hexdigest()
            _verify_tar(tmp,expected)
            with tmp.open('rb')as f:os.fsync(f.fileno())
            tmp.replace(target)
        finally:tmp.unlink(missing_ok=True)
        result={'schema':'pixelworld-verified-run-archive-v1','source_run':str(run),'model_key':m['model_key'],
                'benchmark':benchmark,'run_number':num,'case_id':case,'track':track,'attempt':attempt,
                'archive':target.name,'archive_bytes':target.stat().st_size,'members':expected,
                'verification':'independent SHA256 member readback','completed_utc':utc_now(),
                'known_request_roles':roles,'wire_pair_count':sum(bool(set(v)&{'input.json','native.json'}) and bool(set(v)&{'events.json','response.json'}) for v in roles.values()),
                'wire_scope':'known response IDs from native usage; missing roles are not reconstructed','pruned_source_files':0}
        atomic_json(receipt,result)
        return result


def archive_if_configured(run: Path, *, benchmark: str) -> None:
    """Publish archive status outside the source manifest; leave the run recoverable."""
    root = os.environ.get('PIXELWORLD_VERIFIED_ARCHIVE_ROOT')
    if not root:
        return
    raw = os.environ.get('PIXELWORLD_PROXY_RECORD_ROOT')
    scope = os.environ.get('PIXELWORLD_ARCHIVE_BENCHMARK', benchmark)
    try:
        receipt = archive_closed_attempt(run, Path(root), benchmark=scope,
                                         proxy_logs=Path(raw) if raw else None)
        record = {'status':'verified', 'benchmark':scope, 'run_number':receipt['run_number'],
                  'wire_pair_count':receipt['wire_pair_count'], 'known_requests':len(receipt['known_request_roles'])}
    except (OSError, ValueError, KeyError) as error:
        record = {'status':'failed', 'benchmark':scope, 'error':str(error), 'source_preserved':True}
    atomic_json(run/'verified_archive_status.json', record)
