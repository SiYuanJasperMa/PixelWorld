"""Publish incremental live metadata and saved RGB candidates independently of Pages.

Only public case artifacts are exported. API records, secrets, private paths and
provider reasoning stay private. Sources are read; agent workspaces are untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile
import shutil
import subprocess
import time
import fcntl
import sys
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_bench_review import export_pixels
from pixelworld.smoke_web import clean
from tools.live_evaluation_data import collect as collect_evaluation
from tools.live_case_diagnostics import diagnostics as case_diagnostics, VERSION as DIAGNOSTICS_VERSION
from pixelworld.reuse import run_sources, registry as reuse_registry, public_provenance

PUBLIC_SCHEMA = 3
LARGE_MEDIA_RENDER_LOCK = threading.Lock()
PUBLICATION_ROOT = None
PUBLICATION_DIRTY = set()
PUBLICATION_DIRTY_LOCK = threading.Lock()


def mark_publication_dirty(path):
    if PUBLICATION_ROOT is not None and path.is_relative_to(PUBLICATION_ROOT):
        with PUBLICATION_DIRTY_LOCK:
            PUBLICATION_DIRTY.add('pixelworld/'+str(path.relative_to(PUBLICATION_ROOT)))


def large_media_headroom(spec):
    needed=2*1024**3  # Preview encoder reads native PNGs, not the full packed tensor.
    for limit_path,usage_path in [
        (Path('/sys/fs/cgroup/memory/memory.limit_in_bytes'),Path('/sys/fs/cgroup/memory/memory.usage_in_bytes')),
        (Path('/sys/fs/cgroup/memory.max'),Path('/sys/fs/cgroup/memory.current'))]:
        try:
            limit=int(limit_path.read_text());usage=int(usage_path.read_text())
            return limit-usage >= needed
        except (OSError,ValueError):
            continue
    return True


def export_native_preview(source,target,spec):
    frames=source/'frames';pattern=frames/'%02d.png'
    if not all((frames/f'{i:02d}.png').is_file() for i in range(spec['frames'])):
        raise OSError('Waiting for the complete native PNG candidate')
    target.mkdir(parents=True,exist_ok=True)
    preview=target/'preview.mp4';poster=target/'poster.png'
    if not preview.exists():
        temporary=target/'preview.tmp.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-framerate',str(spec['fps']),
            '-i',str(pattern),'-frames:v',str(spec['frames']),'-an','-c:v','libx264',
            '-preset','veryfast','-crf','18','-maxrate','18M','-bufsize','36M',
            '-pix_fmt','yuv420p','-threads','2','-movflags','+faststart',str(temporary)],
            check=True,timeout=300)
        temporary.replace(preview)
    if not poster.exists():shutil.copyfile(frames/'00.png',poster)
    return {**{k:spec[k] for k in ['width','height','frames','fps']},
            'preview_file':'preview.mp4','layout':'native-size-compressed-preview',
            'native_rgb_retained':True}


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(path, value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.replace(path)
    mark_publication_dirty(path)
    return True


def merge_external_cache(current, external):
    """Accept newly imported immutable entries without losing live updates."""
    for identity, entries in external.items():
        group = current.setdefault(identity, {})
        for key, value in entries.items():
            group.setdefault(key, value)


def save_head_pointer(destination, revision, now=None):
    """Publish an immutable time-bucket pointer to an already-pushed commit."""
    bucket = int(time.time() if now is None else now) // 30 * 30
    day = datetime.fromtimestamp(bucket, timezone.utc).date().isoformat()
    path = destination / 'live-heads' / day / f'{bucket}.json'
    if not path.exists():
        save(path, {'revision': revision, 'bucket_unix': bucket})
    return path


def image_asset(source, destination):
    if not source.is_file():
        return None
    data = source.read_bytes()
    key = hashlib.sha256(data).hexdigest()[:20]
    name = 'assets/' + key + source.suffix
    target = destination / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
    return name


def export_candidate(source, destination, spec, label, utc):
    pixels = source / 'pixels.npz'
    if not pixels.is_file():
        return None
    from pixelworld.util import sha256
    digest = sha256(pixels)[:20]
    prefix = 'assets/' + digest
    target = destination / prefix
    locks=destination/'.asset-locks';locks.mkdir(parents=True,exist_ok=True)
    with (locks/(digest+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if spec['width']*spec['height']*spec['frames']*3 > 100*1024**2:
            with LARGE_MEDIA_RENDER_LOCK:
                if not large_media_headroom(spec):
                    raise OSError('Native media export deferred for container memory headroom')
                video = export_native_preview(source, target, spec)
        else:
            video = export_pixels(source, target, spec)
    if not video:
        return None
    value = {**video, 'label': label, 'utc': utc,'asset_id':digest,
             'pixels': prefix + '/' + video['pixels_file'] if video.get('pixels_file') else None,
             'poster': prefix + '/poster.png'}
    if video.get('preview_file'):value['preview_mp4']=prefix+'/'+video['preview_file']
    if video.get('chunks'):
        value['chunks']=[{**c,'pixels':prefix+'/'+c['pixels_file']}for c in video['chunks']]
    for name in ('inspection.png', 'transition_check.png'):
        if (source / name).is_file():
            value[name.split('.')[0]] = image_asset(source / name, destination)
    if (source / 'generate.py').is_file():
        value['code'] = clean((source / 'generate.py').read_text())
    return value


def case_data(run, destination, previous=None):
    manifest = read(run / 'run_manifest.json')
    spec = read(run / 'media_contract.json')
    budget = read(run / 'logs/token_budget.json')
    usage = manifest.get('token_usage') or {}
    prompt = run / 'input_snapshot/prompt.txt'
    if not prompt.is_file():
        prompt = run / 'workspace/prompt.txt'
    snapshots = sorted((run / 'trace').glob('round_*/snapshot.json'))
    pixels = run / 'workspace/pixels.npz'
    media_stamp = ([pixels.stat().st_mtime_ns, pixels.stat().st_size] if pixels.exists() else []) + [
        [p.parent.name, p.stat().st_mtime_ns] for p in snapshots]
    versions = []
    reused = (previous is not None and previous.get('schema_version') == PUBLIC_SCHEMA
              and previous.get('media_stamp') == media_stamp
              and (previous.get('candidates') or not pixels.is_file()))
    if reused:
        versions = previous.get('candidates', [])
    for path in ([] if reused else snapshots):
        record = read(path)
        try:
            video = export_candidate(path.parent, destination, spec,
                                     f'候选 {len(versions) + 1}', record.get('utc'))
            if video:
                versions.append(video)
        except (OSError, ValueError, KeyError, EOFError, BadZipFile):
            # A snapshot can still be completing. Keep earlier versions visible.
            continue
    if not reused and (not versions or manifest.get('status') == 'complete'):
        try:
            final = export_candidate(run / 'workspace', destination, spec,
                                     '当前保存结果', manifest.get('completed_utc'))
            if final and (not versions or versions[-1].get('asset_id',versions[-1]['pixels']) != final['asset_id']):
                versions.append(final)
        except (OSError, ValueError, KeyError, EOFError, BadZipFile):
            pass
    events = []
    event_path = run / 'logs/events.jsonl'
    if event_path.is_file():
        for line in event_path.read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get('item') or {}
            if event.get('type') == 'item.completed' and item.get('type') != 'reasoning':
                value = {'kind': item.get('type'),
                         'utc': (event.get('_harness_received') or {}).get('utc')}
                for key in ('command', 'aggregated_output', 'text', 'exit_code', 'status'):
                    if key in item:
                        raw = item[key]
                        value[key] = clean(raw) if isinstance(raw, str) else raw
                if item.get('type') == 'full_trace_image_return':
                    value['images'] = []
                    for returned in item.get('returned_images') or []:
                        name = returned.get('path')
                        if not name or returned.get('available') is False:
                            continue
                        source = (event_path.parent / name).resolve()
                        if source.is_relative_to(event_path.parent.resolve()) and source.is_file():
                            image = image_asset(source, destination)
                            if image:
                                value['images'].append(image)
                if len(value.get('aggregated_output', '')) > 30000:
                    value['aggregated_output'] = value['aggregated_output'][:30000] + '\n[网页截取；完整原始输出保留在实验记录中]'
                events.append(value)
            elif event.get('type') in ('turn.failed', 'turn.completed', 'error', 'turn.cancelled'):
                events.append({'kind': event['type'],
                               'utc': (event.get('_harness_received') or {}).get('utc'),
                               'text': clean(str(event.get('message') or event.get('error') or ''))})
    return {'case_id': manifest['case_id'], 'model_key': manifest['model_key'],
            'model': manifest['model'], 'track': manifest['track'],
            'attempt': manifest['attempt'], 'status': manifest['status'],
            'delivery_status': 'stale' if (manifest.get('validation') or {}).get('infra_error') else manifest['status'],
            'started_utc': manifest.get('started_utc'),
            'completed_utc': manifest.get('completed_utc'),
            'output_tokens': budget.get('reported_output_tokens', usage.get('output_tokens')),
            'input_tokens': budget.get('reported_input_tokens', usage.get('input_tokens')),
            'cached_input_tokens': usage.get('cached_input_tokens'),
            'reasoning_output_tokens': usage.get('reasoning_output_tokens'),
            'duration_seconds': manifest.get('duration_seconds'),
            'budget_exhausted': budget.get('exhausted', False),
            'diagnostics':case_diagnostics(run,manifest,budget,events),
            'validation': {k: v for k, v in (manifest.get('validation') or {}).items()
                           if k in ('pass', 'missing_required', 'generate_runs', 'frames')},
            'spec': spec, 'prompt': clean(prompt.read_text()) if prompt.exists() else '',
            'first_frame': image_asset(run / 'input_snapshot/first_frame.png', destination),
            'source_video': image_asset(run / 'input_snapshot/source_video.mp4', destination),
            'candidates': versions, 'events': events, 'media_stamp': media_stamp,
            'schema_version': PUBLIC_SCHEMA}


def upstream_wait(folder: Path, running_cases) -> dict:
    """Distinguish 'generating' from 'queued at the provider with no first token'."""
    oldest = 0
    waiting = 0
    for case in running_cases or []:
        events = None
        for candidate in sorted(folder.glob(f"case_{case}_attempt_*/logs/events.jsonl")):
            events = candidate
        if events is None:
            continue
        try:
            lines = [line for line in events.read_text(errors="replace").splitlines() if line.strip()]
            age = max(0.0, time.time() - events.stat().st_mtime)
        except OSError:
            continue
        kinds = []
        for line in lines[-4:]:
            try:
                kinds.append(json.loads(line).get("type"))
            except ValueError:
                pass
        if len(lines) <= 2 and kinds and kinds[-1] == "turn.started":
            waiting += 1
            oldest = max(oldest, age)
    return {"waiting": waiting, "oldest_seconds": round(oldest)}


def provider_health() -> dict:
    """Last successful upstream call per model, from the local proxy spool."""
    roots = [Path.home() / ".cache/pixelworld-api-spool-active",
             Path.home() / ".cache/pixelworld-api-spool-4k"]
    latest = {}
    now = time.time()
    for root in roots:
        try:
            files = sorted(root.glob("requests*.jsonl"), key=lambda p: p.stat().st_mtime)[-3:]
        except OSError:
            continue
        for path in files:
            try:
                lines = path.read_text(errors="replace").splitlines()[-4000:]
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                model, started = row.get("model"), row.get("started")
                if not isinstance(started, (int, float)) or now - started > 7200:
                    continue
                if row.get("http_status") != 200:
                    continue
                if model not in latest or started > latest[model]:
                    latest[model] = started
    models = {}
    for model, started in latest.items():
        models[model] = {"last_success_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
                         "age_seconds": round(now - started)}
    return {"checked_utc": datetime.now(timezone.utc).isoformat(), "models": models}


def sources(run_root):
    registry = run_root / 'live_sources.json'
    if registry.is_file():
        records = json.loads(registry.read_text())['sources']
        keys = [record['key'] for record in records]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate suite in live_sources registry')
        return [(record['key'], Path(record['runs_root'])) for record in records
                if (Path(record['runs_root']) / 'status.json').is_file()]
    primary = ('physics_iq_720p24fps','physics_iq_4k24fps',
               'robojudge_main720p24fps','pcve_standard720p24fps','phygenbench_t2v_main720p24fps',
               'physics_iq_512w24fps_gpt_native', 'physics_iq', 'physics_iq_gpt_native_v2',
               'physics_iq_seed21_native_v2', 'robojudge', 'robojudge_seed21_native_v2', 'pcve_smoke')
    values = [(name, run_root / 'generations' / name) for name in primary]
    paired = run_root.parent / 'phygen_pixelspace_v2_20261112/generations'
    paired_values = [('phygen_' + name, paired / name) for name in
                     ('t2v', 'i2v_qweninit', 't2v_deepseek', 'i2v_qweninit_deepseek')]
    values = values[:1] + paired_values + values[1:]
    return [(key, path) for key, path in values if (path / 'status.json').is_file()]


def delivered_usage_means(folder):
    """Means over first successful attempts, independent of media-export timing."""
    delivered = {}
    if folder.exists():
        for run, _, provenance in run_sources(folder):
            path = run / 'run_manifest.json'
            manifest = read(path)
            if manifest.get('status') != 'complete' or not manifest.get('validation', {}).get('pass'):
                continue
            case = manifest.get('case_id')
            if provenance or case not in delivered or manifest.get('attempt', 0) < delivered[case].get('attempt', 0):
                delivered[case] = manifest
    output = {'mean_scope': 'first_successful_attempt', 'delivered_usage_cases': len(delivered)}
    for field in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens'):
        values = [(m.get('token_usage') or {}).get(field) for m in delivered.values()]
        values = [x for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
        output['mean_' + field] = sum(values) / len(values) if values else None
        output[field + '_measured_cases'] = len(values)
    return output


def update(run_root, destination, cache, initial_limit=2, suites=None):
    catalogue = {'groups': [], 'updated_utc': datetime.now(timezone.utc).isoformat()}
    for suite, source in sources(run_root):
        if suites is not None and suite not in suites:
            continue
        status = read(source / 'status.json')
        for key, group in status.get('groups', {}).items():
            model, track = key.split('/', 1)
            if model == 'gemini38-flash':
                continue
            identity = suite + '/' + key
            stored = cache.setdefault(identity, {})
            # Current attempts are always first, before any old trace backlog.
            priority = set(group.get('running_cases') or [])
            if group.get('latest_completion'):
                priority.add(group['latest_completion'][1])
            already_complete = (status.get('cases', 0) > 4 and
                                group.get('counts', {}).get('complete') == status.get('cases'))
            folder = source / model / track
            manifests = ([] if already_complete and not stored or not folder.exists() else run_sources(folder))
            manifests.sort(key=lambda p: (p[0].name.rsplit('_attempt_', 1)[0][5:] not in priority, p[1]))
            missed = 0
            for run, cache_key, provenance in manifests:
                path = run / 'run_manifest.json'
                case = run.name.rsplit('_attempt_', 1)[0][5:]
                if case not in priority and cache_key not in stored:
                    if missed >= initial_limit:
                        continue
                    missed += 1
                if not path.is_file():
                    continue
                token = run / 'logs/token_budget.json'
                stamp = [PUBLIC_SCHEMA, path.stat().st_mtime_ns,
                         token.stat().st_mtime_ns if token.exists() else 0,
                         [(p.name, p.stat().st_mtime_ns) for p in (run / 'trace').glob('round_*/snapshot.json')],
                         (run / 'logs/events.jsonl').stat().st_mtime_ns if (run / 'logs/events.jsonl').exists() else 0]
                if stored.get(cache_key, {}).get('stamp') != stamp:
                    try:
                        relative = identity + '/' + cache_key + '.json'
                        detail = case_data(run, destination, previous=read(destination / relative))
                    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
                        continue
                    if provenance:
                        detail['reused'] = True
                        detail['provenance'] = public_provenance(provenance)
                    relative = identity + '/' + cache_key + '.json'
                    save(destination / relative, detail)
                    stored[cache_key] = {'stamp': stamp, 'detail': relative, 'case': case,
                                        'attempt': detail['attempt'], 'status': detail['delivery_status'],
                                        'raw_status': detail['status'],
                                        'output_tokens': detail['output_tokens'],
                                        'candidate_count': len(detail['candidates']),
                                        'poster': detail['candidates'][-1]['poster'] if detail['candidates'] else detail['first_frame']}
            cases = {}
            for value in stored.values():
                cases.setdefault(value['case'], []).append({k: v for k, v in value.items() if k != 'stamp'})
            for history in cases.values():
                newest = max(a['attempt'] for a in history)
                for attempt in history:
                    if attempt['status'] == 'running' and attempt['attempt'] < newest:
                        attempt['status'] = 'stale'
            completed = [min([a for a in history if a['status'] == 'complete'], key=lambda a: a['attempt'])
                         for history in cases.values() if any(a['status'] == 'complete' for a in history)]
            measured = [a['output_tokens'] for a in completed if a.get('output_tokens') is not None]
            payload = {'suite': suite, 'model_key': model, 'track': track,
                       'counts': group.get('counts') or {}, 'cases_total': status.get('cases'),
                       'latest_completion': group.get('latest_completion'),
                       'mean_duration_seconds': group.get('mean_duration_seconds'),
                       'mean_output_tokens_cached': sum(measured) / len(measured) if measured else None,
                       'token_mean_cases': len(measured),
                       'upstream_wait': upstream_wait(folder, group.get('running_cases')),
                       'updated_utc': status.get('updated_utc'), 'cases': cases}
            relative = identity + '/index.json'
            save(destination / relative, payload)
            catalogue['groups'].append({k: payload[k] for k in ('suite', 'model_key', 'track', 'counts', 'cases_total', 'mean_duration_seconds', 'mean_output_tokens_cached', 'token_mean_cases', 'upstream_wait')})
            catalogue['groups'][-1]['path'] = relative
    catalogue['provider_health'] = provider_health()
    save(destination / 'catalogue.json', catalogue)


def render_case_set(folder, case, identity, render_root, saved):
    """All potentially slow case I/O stays off the publication thread."""
    locks=render_root/'.case-locks';locks.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256((identity+'/'+str(case)).encode()).hexdigest()
    with (locks/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _render_case_set(folder,case,identity,render_root,saved)


def _render_case_set(folder, case, identity, render_root, saved):
    rows = []
    for run, cache_key, provenance in run_sources(folder, case):
        manifest = run / 'run_manifest.json'
        if not manifest.is_file():
            continue
        relative = identity + '/' + cache_key + '.json'
        token = run / 'logs/token_budget.json'
        events = run / 'logs/events.jsonl'
        stamp = [PUBLIC_SCHEMA, manifest.stat().st_mtime_ns,
                 token.stat().st_mtime_ns if token.exists() else 0,
                 events.stat().st_mtime_ns if events.exists() else 0,
                 [(p.parent.name,p.stat().st_mtime_ns) for p in (run/'trace').glob('round_*/snapshot.json')],
                 DIAGNOSTICS_VERSION]
        if saved.get(cache_key,{}).get('stamp') == stamp:
            continue
        try:
            previous=read(render_root/relative) or read(render_root.parent/'pixelworld'/relative)
            detail = case_data(run, render_root, previous=previous)
        except (OSError,ValueError,KeyError,EOFError,BadZipFile):
            continue
        if provenance:
            detail['reused'] = True
            detail['provenance'] = public_provenance(provenance)
        save(render_root/relative,detail)
        rows.append((cache_key,relative,stamp,detail))
    return rows


def referenced_assets(detail):
    values = [detail.get('first_frame'), detail.get('source_video')]
    for video in detail.get('candidates',[]):
        values += [video.get(k) for k in ('pixels','preview_mp4','poster','inspection','transition_check')]
        values += [c.get('pixels')for c in video.get('chunks',[])]
    for event in detail.get('events',[]):
        values += event.get('images',[])
    return set(path for path in values if path)


def render_backfill_set(folder,identity,render_root,saved,excluded=(),limit=4):
    """Export a bounded historical batch in workers reserved for old cases."""
    cases=set()
    for run, cache_key, provenance in run_sources(folder):
        case=run.name[5:].rsplit('_attempt_',1)[0]
        if case in excluded:continue
        manifest=run/'run_manifest.json'
        try:
            stamp=manifest.stat().st_mtime_ns
        except OSError:continue
        cached=saved.get(cache_key,{}).get('stamp') or []
        if len(cached)<2 or cached[0]!=PUBLIC_SCHEMA or cached[1]!=stamp or cached[-1]!=DIAGNOSTICS_VERSION:
            cases.add(case)
    rows=[]
    for case in sorted(cases)[:limit]:
        rows.extend(render_case_set(folder,case,identity,render_root,saved))
    return rows


def merge_render_jobs(destination,cache,pending,render_root,published_assets=None):
    for key,future in list(pending.items()):
        if not future.done():
            continue
        identity,_ = key
        try:
            rows = future.result()
        except Exception:
            rows = []
        for name,relative,stamp,detail in rows:
            for asset in referenced_assets(detail):
                if published_assets is not None and asset in published_assets:
                    continue  # Immutable asset is already in the committed public tree.
                target=destination/asset
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temp=target.with_name(target.name+'.tmp')
                    shutil.copyfile(render_root/asset,temp)
                    temp.replace(target)
                    mark_publication_dirty(target)
            save(destination/relative,detail)
            cache.setdefault(identity,{})[name]={'stamp':stamp,'detail':relative,
                'case':detail['case_id'],'attempt':detail['attempt'],
                'status':detail['delivery_status'],'raw_status':detail['status'],
                'output_tokens':detail['output_tokens'],'candidate_count':len(detail['candidates']),
                'reused':detail.get('reused',False),
                'poster':detail['candidates'][-1]['poster'] if detail['candidates'] else detail['first_frame']}
        del pending[key]


def async_update(run_root,destination,cache,pool,pending,render_root,
                 history_pool=None,history_pending=None,history_cursor=None,published_assets=None):
    merge_render_jobs(destination,cache,pending,render_root,published_assets)
    if history_pending is not None:
        merge_render_jobs(destination,cache,history_pending,render_root,published_assets)
    catalogue={'groups':[],'updated_utc':datetime.now(timezone.utc).isoformat()}
    historical=[]
    for suite,source in sources(run_root):
        status=read(source/'status.json')
        for key,group in status.get('groups',{}).items():
            model,track=key.split('/',1)
            if model=='gemini38-flash':
                continue
            identity=suite+'/'+key;stored=cache.setdefault(identity,{})
            desired=list(group.get('running_cases') or [])
            if group.get('latest_completion'):
                desired.append(group['latest_completion'][1])
            if suite.startswith('phygen_'):
                # Four-case smoke discovery also runs in the I/O worker.
                desired=[None]
            for case in desired:
                job=(identity,case)
                if job not in pending and (history_pending is None or job not in history_pending) and len(pending)<48:
                    pending[job]=pool.submit(render_case_set,source/model/track,case,identity,render_root,dict(stored))
            if (history_pool is not None and status.get('cases',0)>4):
                historical.append((identity,source/model/track,dict(stored),desired))
            cases={}
            for value in stored.values():
                cases.setdefault(value['case'],[]).append({k:v for k,v in value.items() if k!='stamp'})
            for history in cases.values():
                newest=max(a['attempt'] for a in history)
                for attempt in history:
                    if attempt['status']=='running' and attempt['attempt']<newest:attempt['status']='stale'
            completed=[min([a for a in h if a['status']=='complete'],key=lambda a:a['attempt'])
                       for h in cases.values() if any(a['status']=='complete' for a in h)]
            measured=[a['output_tokens'] for a in completed if a.get('output_tokens') is not None]
            payload={'suite':suite,'model_key':model,'track':track,'counts':group.get('counts') or {},
                'paused':(source/'STOP').exists(),
                'cases_total':status.get('cases'),'updated_utc':status.get('updated_utc'),'cases':cases,
                'latest_completion':group.get('latest_completion'),
                'mean_duration_seconds':group.get('mean_duration_seconds'),
                'mean_output_tokens_cached':sum(measured)/len(measured) if measured else None,
                'token_mean_cases':len(measured),
                'upstream_wait':upstream_wait(source/model/track,group.get('running_cases'))}
            payload.update(delivered_usage_means(source/model/track))
            payload['reused_cases'] = sum(k == model and t == track for k,t,c in reuse_registry(source))
            relative=identity+'/index.json';save(destination/relative,payload)
            catalogue['groups'].append({k:v for k,v in payload.items() if k not in ('cases','updated_utc')})
            catalogue['groups'][-1]['path']=relative
    catalogue['provider_health']=provider_health()
    save(destination/'catalogue.json',catalogue)
    if history_pool is not None and history_pending is not None and historical:
        start=history_cursor[0] % len(historical) if history_cursor is not None else 0
        ordered=historical[start:]+historical[:start]
        for index,(identity,folder,saved,desired) in enumerate(ordered):
            if len(history_pending)>=2:break
            job=(identity,'_history')
            if job in history_pending:continue
            excluded=set(desired)|{case for (name,case) in pending if name==identity}
            history_pending[job]=history_pool.submit(render_backfill_set,folder,identity,render_root,saved,excluded)
            if history_cursor is not None:history_cursor[0]=(start+index+1)%len(historical)


def main():
    global PUBLICATION_ROOT
    p = argparse.ArgumentParser()
    p.add_argument('--run-root', type=Path, required=True)
    p.add_argument('--checkout', type=Path, required=True)
    p.add_argument('--interval', type=float, default=30)
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    root = args.checkout.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / '.publisher.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    destination = root / 'pixelworld'
    PUBLICATION_ROOT = destination
    cache_path = root / '.publisher_cache.json'
    cache = read(cache_path)
    pool=ThreadPoolExecutor(max_workers=2)
    evaluation_pool=ThreadPoolExecutor(max_workers=1)
    history_pool=ThreadPoolExecutor(max_workers=1)
    history_pending={}
    history_cursor=[0]
    evaluation_future=None
    evaluation_error=None
    pending={}
    render_root=root/'.render-cache'
    render_root.mkdir(exist_ok=True)
    tracked=subprocess.check_output(['git','ls-files','-z','--','pixelworld/assets'],cwd=root).decode().split('\0')
    published_assets={name.removeprefix('pixelworld/') for name in tracked if name}
    for command in [
        ['git','diff','--name-only','-z','--','pixelworld'],
        ['git','diff','--cached','--name-only','-z','--','pixelworld'],
        ['git','ls-files','--others','--exclude-standard','-z','--','pixelworld']]:
        PUBLICATION_DIRTY.update(p for p in subprocess.check_output(command,cwd=root).decode().split('\0') if p)
    while True:
        start = time.monotonic()
        try:
            # A one-time importer may add already-sanitized historical details
            # while this process is alive. Pull only missing immutable records;
            # live in-memory stamps always remain authoritative.
            merge_external_cache(cache, read(cache_path))
            published_revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
            async_update(args.run_root.resolve(),destination,cache,pool,pending,render_root,
                         history_pool,history_pending,history_cursor,published_assets)
            if evaluation_future is not None and evaluation_future.done():
                try:
                    save(destination/'evaluation.json',evaluation_future.result())
                    evaluation_error=None
                except Exception as exc:
                    evaluation_error=str(exc)
                evaluation_future=None
            if evaluation_future is None:
                evaluation_future=evaluation_pool.submit(collect_evaluation,args.run_root.resolve())
            save_head_pointer(destination, published_revision)
            save(cache_path, cache)
            with PUBLICATION_DIRTY_LOCK:
                changed=sorted(p for p in PUBLICATION_DIRTY if not p.endswith('.tmp'))
            for batch_start in range(0,len(changed),1000):
                subprocess.run(['git','add','--sparse','--',*changed[batch_start:batch_start+1000]],cwd=root,check=True)
            if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=root).returncode:
                subprocess.run(['git', 'commit', '-m', 'Refresh live PixelWorld progress and saved candidates'], cwd=root, check=True,
                               stdout=subprocess.DEVNULL)
            subprocess.run(['git', 'push', 'origin', 'pixelworld-live'], cwd=root, check=True, timeout=60)
            with PUBLICATION_DIRTY_LOCK:
                PUBLICATION_DIRTY.difference_update(changed)
            save(root / '.publisher_status.json', {'pid': __import__('os').getpid(), 'updated_unix': time.time(),
                 'duration_seconds': time.monotonic() - start, 'groups': len(cache), 'status': 'ok',
                 'pending_case_reads':len(pending),'pending_history_batches':len(history_pending),
                 'evaluation_reader_error':evaluation_error})
        except Exception as exc:
            save(root / '.publisher_status.json', {'pid': __import__('os').getpid(), 'updated_unix': time.time(),
                 'status': 'error', 'error': str(exc)})
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
