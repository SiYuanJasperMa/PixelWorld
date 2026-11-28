"""Private, verified closed-run archives and byte-identical scratch cleanup."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from time import time_ns
import zipfile

from .util import atomic_json, sha256, utc_now

SAFE = re.compile(r'[A-Za-z0-9_.-]+$')
RID = re.compile(r'resp_[a-zA-Z0-9]+$')
WIRE_ROLES = ('input.json','native.json','events.json','response.json','error.txt')
RUN_DIRECTORIES = ('input_snapshot', 'logs', 'trace', 'recording')
RUN_FILES = ('run_manifest.json', 'task_prompt.txt', 'media_contract.json', 'permissions_profile.json', 'model_catalog.json')
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


def _preferred_wire_path(rid, suffix, roots):
    """Prefer a retained authorized copy while closed logs migrate."""
    for _ in range(3):
        matches=[]
        for root in roots:
            p=Path(root)/f'{rid}.{suffix}'
            if p.is_file() and not p.is_symlink() and p.resolve()==p:matches.append(p)
        if not matches:return None
        try:
            if len(matches)>1 and len({sha256(p) for p in matches})!=1:
                raise ValueError('Conflicting original API records across authorized roots')
            return matches[-1]
        except FileNotFoundError:
            # A verified mover can remove the old copy after discovery.
            # Re-discover the same response ID and role, never another file.
            continue
    raise FileNotFoundError(f'Original API record changed locations: {rid}.{suffix}')


def _wire_source(name, source, roots):
    parts=Path(name).parts
    if (len(parts)==3 and parts[0]=='api_originals' and RID.fullmatch(parts[1])
            and parts[2] in WIRE_ROLES):
        return _preferred_wire_path(parts[1],parts[2],roots) or source
    return source


def _wire_files(run, log_root, request_ids=(), fallback_roots=()):
    ids=list(dict.fromkeys(rid for rid in request_ids if isinstance(rid, str) and RID.fullmatch(rid)))
    ledger=run/'recording/api_usage.jsonl'
    if ledger.is_file():
        with ledger.open() as f:
            for line in f:
                try:x=json.loads(line)
                except ValueError:continue
                rid=x.get('response_id')
                if isinstance(rid,str) and RID.fullmatch(rid) and rid not in ids:ids.append(rid)
    files=[]; roles={}
    roots=([Path(log_root)] if log_root is not None else []) + [Path(p).resolve(strict=True) for p in fallback_roots]
    if any(not p.is_dir() for p in roots):
        raise ValueError('Proxy log roots must be directories')
    for rid in ids:
        roles[rid]=[]
        for suffix in WIRE_ROLES:
            selected=_preferred_wire_path(rid,suffix,roots)
            if selected:
                files.append((f'api_originals/{rid}/{suffix}',selected));roles[rid].append(suffix)
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


def _prune_reproducible(run, manifest, hashes, roles, verified_receipt):
    """Prune only byte-identical staging copies of the final archived tensor."""
    rel = 'workspace/pixels.npz'
    final = run/rel
    workspace = final.parent
    if (not verified_receipt.is_file() or workspace.is_symlink() or
            manifest.get('status') != 'complete' or manifest.get('validation', {}).get('pass') is not True or
            manifest.get('validation', {}).get('png_tensor_exact') is not True or
            not roles or not all('input.json' in v and {'events.json', 'response.json'}.intersection(v)
                                 for v in roles.values()) or
            rel not in hashes or manifest.get('pixels_sha256') != hashes[rel] or
            final.is_symlink() or not final.is_file()):
        return []
    names = ('pixels.packing.npy', 'pixels.npz.partial')
    if not any((workspace/name).is_file() and not (workspace/name).is_symlink() for name in names):
        return []
    if sha256(final) != hashes[rel]:
        return []
    def fingerprint(entry):
        return entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns

    removed = []
    for name in names:
        source = workspace/name
        try:
            before = source.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                continue
            with os.fdopen(os.open(source, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK), 'rb') as f:
                if fingerprint(os.fstat(f.fileno())) != fingerprint(before):
                    continue
                if name.endswith('.npy'):
                    with zipfile.ZipFile(final) as z:
                        member = z.getinfo('pixels.npy')
                        if before.st_size != member.file_size:
                            continue
                        with z.open(member) as saved:
                            expected = hashlib.file_digest(saved, 'sha256').hexdigest()
                else:
                    expected = hashes[rel]
                same_bytes = hashlib.file_digest(f, 'sha256').hexdigest() == expected
            if same_bytes and fingerprint(source.lstat()) == fingerprint(before):
                source.unlink()
                removed.append({'path': f'workspace/{name}', 'bytes': before.st_size,
                                'sha256': expected, 'proof': 'byte-identical to verified final tensor'})
        except (OSError, KeyError, zipfile.BadZipFile):
            continue  # Only exact, readable duplicates are eligible.
    return removed


def _external_files(extra_files):
    files=[]
    for name, source in sorted((extra_files or {}).items()):
        relative=Path(name)
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts or
                relative.parts[0] not in ('configuration', 'evaluations') or
                any(not SAFE.fullmatch(part) or part in ('.', '..') for part in relative.parts)):
            raise ValueError('Unsafe archive attachment name')
        source=Path(source).absolute()
        if source.is_symlink() or source.resolve()!=source or not source.is_file():
            raise ValueError('Archive attachments must be explicit regular files')
        files.append((relative.as_posix(),source))
    return files


def _archive_identity(run, manifest_sha256, wire_files, extras, request_ids=(), wire_roots=()):
    def signature(name,path):
        for _ in range(3):
            try:
                st=_wire_source(name,path,wire_roots).stat()
                return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
            except FileNotFoundError:continue
        raise FileNotFoundError(path)
    value={'source_run':str(run), 'manifest_sha256':manifest_sha256,
           'known_request_ids':sorted(request_ids),
           'wire_members':{name:signature(name,path) for name,path in wire_files},
           'attachments':{name:sha256(path) for name,path in extras}}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest(),value


def latest_archive_receipt(run, root, *, benchmark, proxy_logs=None, request_ids=(), extra_files=None, proxy_log_fallbacks=()):
    """Return only the exact current archive; changed revisions remain available."""
    run=Path(run).resolve();root=Path(root).absolute()
    manifest=json.loads((run/'run_manifest.json').read_text())
    parent=root/_safe(benchmark)/_safe(manifest['model_key']);index=parent/'archive_index.json'
    if not index.is_file():return None
    key=f"{_safe(manifest['track'])}/{_safe(str(manifest['case_id']))}/{int(manifest['attempt'])}"
    number=json.loads(index.read_text()).get(key)
    if number is None:return None
    path=parent/f'run_{number:04d}'/'verified.json'
    if not path.is_file() or path.is_symlink():return None
    old=json.loads(path.read_text());target=path.parent/'artifacts.tar.gz'
    if target.is_symlink() or not target.is_file() or target.stat().st_size!=old.get('archive_bytes'):
        raise ValueError('Existing verified archive is missing or changed')
    wire,roles=_wire_files(run,proxy_logs,request_ids,proxy_log_fallbacks);extras=_external_files(extra_files)
    wire_roots=([Path(proxy_logs)] if proxy_logs is not None else [])+list(proxy_log_fallbacks)
    manifest_hash=sha256(run/'run_manifest.json')
    identity,fields=_archive_identity(run,manifest_hash,wire,extras,roles,wire_roots)
    if old.get('archive_identity')==identity:return old
    # An inode/path change from verified log migration is not a new recording.
    # Prove changed members against the independently verified tar digests and
    # keep the original tar and receipt unchanged.
    members=old.get('members',{})
    old_wire={name for name in members if name.startswith('api_originals/')}
    old_extras={name for name in members if name.startswith(('configuration/','evaluations/'))}
    if (old.get('source_run')==str(run) and members.get('run_manifest.json')==manifest_hash
            and old.get('known_request_roles')==roles and old_wire=={name for name,_ in wire}
            and old_extras=={name for name,_ in extras}
            and all(members.get(name)==sha256(source) for name,source in extras)):
        reuse=path.parent/f'recording_relocation_{identity}.json'
        original_receipt_hash=sha256(path)
        if reuse.is_file():
            proof=json.loads(reuse.read_text())
            if (proof.get('archive_identity')==identity and proof.get('original_receipt_sha256')==original_receipt_hash):
                return {**old,'archive_identity':identity,'recording_relocation_verified':True}
        previous=old.get('archive_identity_fields',{}).get('wire_members',{})
        changed=[(name,source) for name,source in wire if previous.get(name)!=fields['wire_members'][name]]
        if all(sha256(_wire_source(name,source,wire_roots))==members[name] for name,source in changed):
            atomic_json(reuse,{'archive_identity':identity,'original_receipt_sha256':original_receipt_hash,
                'verified_utc':utc_now(),'changed_wire_members':[name for name,_ in changed],
                'proof':'changed member SHA256 equals verified archived bytes','original_archive_unchanged':True})
            return {**old,'archive_identity':identity,'recording_relocation_verified':True}
    # Compatibility for earlier receipts with no additional attachments.
    if (not old.get('archive_identity') and not extras and old.get('source_run')==str(run)
            and old.get('members',{}).get('run_manifest.json')==sha256(run/'run_manifest.json')
            and set(old.get('known_request_roles',{}))==set(roles)
            and all(name in old.get('members',{}) for name,_ in wire)):
        return old
    return None


def archive_closed_attempt(run:Path, root:Path, *, benchmark:str, proxy_logs:Path|None=None,
                           prune_reproducible:bool=False, request_ids=(), extra_files=None, proxy_log_fallbacks=()) -> dict:
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
    wire_files,roles=_wire_files(run,proxy_logs,request_ids,proxy_log_fallbacks)
    extras=_external_files(extra_files)
    manifest_hash=sha256(run/'run_manifest.json')
    wire_roots=([proxy_logs] if proxy_logs is not None else [])+list(proxy_log_fallbacks)
    identity,identity_fields=_archive_identity(run,manifest_hash,wire_files,extras,roles,wire_roots)
    parent=root/_safe(benchmark)/_safe(m['model_key']);parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if parent.resolve()!=parent:raise ValueError('Archive hierarchy contains a symlink')
    case,track,attempt=_safe(str(m['case_id'])),_safe(m['track']),int(m['attempt'])
    key=f'{track}/{case}/{attempt}'; index=parent/'archive_index.json'
    with (parent/'.archive_lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=json.loads(index.read_text()) if index.is_file() else {}
        previous_number=rows.get(key)
        previous=None
        if previous_number is not None:
            previous_path=parent/f'run_{previous_number:04d}'/'verified.json'
            if previous_path.is_file():
                previous=json.loads(previous_path.read_text())
                exact=latest_archive_receipt(run,root,benchmark=benchmark,proxy_logs=proxy_logs,
                                            request_ids=request_ids,extra_files=extra_files,proxy_log_fallbacks=proxy_log_fallbacks)
                if exact is not None:
                    if prune_reproducible:
                        archive=previous_path.parent/'artifacts.tar.gz'
                        _verify_tar(archive,exact['members'])
                        removed=_prune_reproducible(run,m,exact['members'],exact.get('known_request_roles',{}),previous_path)
                        if removed:
                            record={'verified_utc':utc_now(),'archive_identity':identity,'removed':removed,
                                    'source_manifest_sha256':manifest_hash}
                            atomic_json(previous_path.parent/f'pruning_{time_ns()}.json',record)
                            return {**exact,'pruned_source_files':len(removed),'pruning':removed}
                    return exact
        if previous_number is None or previous is not None:
            rows[key]=max(rows.values(),default=0)+1;atomic_json(index,rows)
        num=rows[key];dest=parent/f'run_{num:04d}'
        dest.mkdir(mode=0o700,exist_ok=True)
        if dest.resolve()!=dest:raise ValueError('Archive destination contains a symlink')
        lineage=dest/'revision.json'
        if not lineage.exists():
            atomic_json(lineage,{'previous_run_number':previous_number if previous else None,
                                'previous_manifest_sha256':previous.get('members',{}).get('run_manifest.json') if previous else None,
                                'reason':'source_or_recording_revision' if previous else 'initial_archive',
                                'archive_identity':identity})
    with (dest/'.archive_lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        receipt=dest/'verified.json';target=dest/'artifacts.tar.gz';tmp=dest/'artifacts.part'
        if receipt.is_file():
            old=json.loads(receipt.read_text())
            with (run/'run_manifest.json').open('rb')as f:current=hashlib.file_digest(f,'sha256').hexdigest()
            if (old['source_run'],old['members']['run_manifest.json'],old['archive_bytes'])!=(str(run),current,target.stat().st_size) or old.get('archive_identity')!=identity:
                raise ValueError('Existing verified archive represents a different source revision')
            return old
        files=list(_closed_files(run));files.extend(wire_files);files.extend(extras)
        expected={}
        try:
            with tarfile.open(tmp,'w:gz',compresslevel=1)as tar:
                for name,src in files:
                    src=_wire_source(name,src,wire_roots)
                    if src.resolve()!=src or name in expected:raise ValueError('Untrusted source member')
                    try:descriptor=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    except FileNotFoundError:
                        src=_wire_source(name,src,wire_roots)
                        descriptor=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    with os.fdopen(descriptor,'rb')as handle:
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
            if sha256(run/'run_manifest.json')!=manifest_hash:
                raise ValueError('Closed manifest changed while archiving; source preserved')
            with tmp.open('rb')as f:os.fsync(f.fileno())
            tmp.replace(target)
        finally:tmp.unlink(missing_ok=True)
        result={'schema':'pixelworld-verified-run-archive-v1','source_run':str(run),'model_key':m['model_key'],
                'benchmark':benchmark,'run_number':num,'case_id':case,'track':track,'attempt':attempt,
                'archive':target.name,'archive_bytes':target.stat().st_size,'members':expected,
                'verification':'independent SHA256 member readback','completed_utc':utc_now(),
                'known_request_roles':roles,'wire_pair_count':sum(bool(set(v)&{'input.json','native.json'}) and bool(set(v)&{'events.json','response.json'}) for v in roles.values()),
                'wire_scope':'explicit response IDs from native usage or exact proxy thread index; missing roles are not reconstructed',
                'pruned_source_files':0,'archive_identity':identity,'archive_identity_fields':identity_fields,
                'revision':json.loads(lineage.read_text()),
                'configuration_scope':'provided registry configuration at archival; original launch facts remain in frozen manifest/task'}
        atomic_json(receipt,result)
        if prune_reproducible:
            try:
                removed = _prune_reproducible(run, m, expected, roles, receipt)
                if removed:
                    result.update(pruned_source_files=len(removed), pruning=removed)
                    atomic_json(receipt, result)
            except OSError as error:
                # A cleanup error must not invalidate an already verified archive.
                result['prune_warning'] = type(error).__name__
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
                                         proxy_logs=Path(raw) if raw else None,
                                         prune_reproducible=os.environ.get('PIXELWORLD_ARCHIVE_PRUNE_REPRODUCIBLE', '1') == '1')
        record = {'status':'verified', 'benchmark':scope, 'run_number':receipt['run_number'],
                  'wire_pair_count':receipt['wire_pair_count'], 'known_requests':len(receipt['known_request_roles']),
                  'pruned_source_files':receipt['pruned_source_files']}
    except (OSError, ValueError, KeyError) as error:
        record = {'status':'failed', 'benchmark':scope, 'error':str(error), 'source_preserved':True}
    atomic_json(run/'verified_archive_status.json', record)
