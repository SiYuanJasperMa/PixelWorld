"""Freeze public previews and complete sanitized traces without modifying live data."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile

from pixelworld.reuse import run_sources
from tools.live_case_diagnostics import public_text


def sanitize(value):
    if isinstance(value, dict):
        def public_key(key):
            cleaned=public_text(key)
            return cleaned+'#'+hashlib.sha256(key.encode()).hexdigest()[:10] if cleaned!=key else key
        return {public_key(key): ('[redacted]' if key.lower() in
            {'authorization', 'password', 'api_key', 'access_token', 'secret_key'}
            else sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'\bhf_[A-Za-z0-9]{20,}', '[redacted]', public_text(value))
    return value


def public_case(data):
    data = sanitize(data)
    if isinstance(data, dict) and 'case_id' in data and 'candidates' in data:
        data['first_frame'] = None
        data['source_video'] = None
        for candidate in data['candidates']:
            for key in ('pixels', 'inspection', 'transition_check'):
                candidate[key] = None
            candidate.pop('chunks', None)
        for event in data.get('events', []):
            event.pop('images', None)
    return data


def json_copy(source, target):
    data = public_case(json.loads(source.read_text()))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    return data


def freeze(bundle, destination):
    if destination.exists():
        raise FileExistsError('Use a new snapshot; an uploader may own an existing snapshot')
    destination.mkdir(parents=True)
    source = bundle / 'publication/pixelworld'
    catalogue = json_copy(source / 'catalogue.json', destination / 'data/catalogue.json')
    json_copy(source / 'catalogue.json', destination / 'metadata/catalogue.json')
    media = set()
    details = 0
    for group in catalogue['groups']:
        relative = Path(group['path']).parent
        for path in (source / relative).glob('*.json'):
            data = json_copy(path, destination / 'data' / relative / path.name)
            for candidate in data.get('candidates', []):
                media.update(candidate[key] for key in ('preview_mp4', 'poster') if candidate.get(key))
            details += 'case_id' in data
    for name in sorted(media):
        relative = Path(name)
        path = (source / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(source.resolve()):
            raise ValueError('Invalid public media path')
        if path.suffix not in ('.mp4', '.png', '.jpg') or not path.is_file():
            continue
        target = destination / 'data' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(path, target)  # Immutable media only; copied JSON is sanitized separately.
    report = {'updated_utc': catalogue['updated_utc'], 'groups': len(catalogue['groups']),
              'case_details': details, 'referenced_media': len(media)}
    (destination / 'metadata/delivery.json').write_text(json.dumps(report, indent=2))
    return report


TRACE_FORMAT_VERSION = 3

TRACE_FILES = ('logs/native/events.native.jsonl', 'logs/native/stderr.log',
    'logs/native_delivery.json', 'logs/native_launch.json', 'run_manifest.json', 'media_contract.json', 'task_prompt.txt',
    'logs/native_events.jsonl','logs/native_session.jsonl','logs/native_launch.json',
    'input_snapshot/prompt.txt', 'logs/events.jsonl', 'logs/events.raw.jsonl',
    'logs/token_budget.json', 'logs/full_rollout.jsonl', 'workspace/generate.py',
    'logs/tool_calls.json', 'logs/full_trace_status.json',
    'logs/stderr.log', 'logs/submission_receipt.json', 'logs/submission_boundary.json',
    'logs/manifest_before_config_reclassification.json',
    'workspace/GENERATION_RULES.md', 'workspace/task_spec.json', 'workspace/artifact_io.py',
    'workspace/inspection.png', 'workspace/transition_check.png')


def trace_names(run):
    names=list(TRACE_FILES)
    for folder,patterns in [('logs/submission_requests',('*.json',)),
                            ('logs/tool_images',('*.png','*.jpg','*.jpeg','*.webp')),
                            ('trace',('round_*/generate.py','round_*/snapshot.json')),
                            ('workspace/_pixelworld_io',('*.py','*.html')),
                            ('workspace/.tmp/harness-history',('*.json','*.png','*.jpg','*.webp'))]:
        for pattern in patterns:
            names.extend(str(p.relative_to(run)) for p in (run/folder).glob(pattern) if p.is_file())
    return sorted(set(names))


def public_blobs(run):
    """Retain spilled text/image messages while rebasing any redacted text hashes."""
    records={};replacements={}
    for path in (run/'logs/rollout-blobs/sha256').glob('*/*'):
        if not path.is_file():continue
        original=path.read_bytes()
        try:
            text=original.decode('utf-8')
        except UnicodeDecodeError:
            continue
        cleaned=sanitize(text).encode()
        digest=hashlib.sha256(cleaned).hexdigest()
        records['logs/rollout-blobs/sha256/'+digest[:2]+'/'+digest]=cleaned
        old='traex-rollout-blob:v1:sha256:'+path.name+':'+str(len(original))
        replacements[old]='traex-rollout-blob:v1:sha256:'+digest+':'+str(len(cleaned))
    return records,replacements


def add_trace_run(archive,run,prefix=''):
    blobs,replacements=public_blobs(run)
    contents=dict(blobs)
    for name in trace_names(run):
        path=run/name
        if not path.is_file():continue
        if path.suffix in ('.png','.jpg','.jpeg','.webp'):
            contents[name]=path.read_bytes()
            continue
        text=path.read_text(errors='replace')
        if path.suffix=='.json':
            text=json.dumps(sanitize(json.loads(text)),ensure_ascii=False)
        elif path.suffix=='.jsonl':
            lines=[]
            for line in text.splitlines():
                try:lines.append(json.dumps(sanitize(json.loads(line)),ensure_ascii=False))
                except ValueError:lines.append(sanitize(line))
            text='\n'.join(lines)+'\n'
        else:text=sanitize(text)
        for original,public in replacements.items():text=text.replace(original,public)
        contents[name]=text.encode()
    for name,content in contents.items():
        info=tarfile.TarInfo((prefix+'/' if prefix else '')+name)
        info.size=len(content);info.mode=0o644
        archive.addfile(info,io.BytesIO(content))


def export_trace_run(run,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.tmp')
    with tarfile.open(temporary,'w:gz',compresslevel=3) as archive:
        add_trace_run(archive,run)
    temporary.replace(destination)
    return destination


def traces(bundle, destination):
    records = json.loads((bundle / 'live_sources.json').read_text())['sources']
    inventory = []
    for record in records:
        source = Path(record['runs_root'])
        status = json.loads((source / 'status.json').read_text())
        for group in status['groups']:
            model, track = group.split('/', 1)
            target = destination / 'traces' / record['benchmark'] / (model + '.tar.gz')
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with tarfile.open(target, 'w:gz', compresslevel=3) as archive:
                for run, _, provenance in run_sources(source / model / track):
                    manifest_path = run / 'run_manifest.json'
                    if not manifest_path.is_file():
                        continue
                    manifest = json.loads(manifest_path.read_text())
                    if manifest.get('status') == 'running':
                        continue
                    count += 1
                    origin = 'reused' if provenance else 'native'
                    prefix = '/'.join((record['benchmark'], model, track, origin, run.name))
                    add_trace_run(archive,run,prefix)
            item = {'benchmark': record['benchmark'], 'model': model, 'attempt_records': count,
                    'archive': str(target.relative_to(destination)), 'bytes': target.stat().st_size,
                    'tool_images_included':True,'code_revisions_included':True,'spilled_blobs_rebased_after_redaction':True}
            inventory.append(item)
            print(json.dumps(item), flush=True)
    path = destination / 'metadata/trace_inventory.json'
    path.write_text(json.dumps(inventory, indent=2))
    return inventory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--upload', action='store_true')
    parser.add_argument('--token-file', type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(args.bundle, args.destination)), flush=True)
    traces(args.bundle, args.destination)
    if args.upload:
        os.environ['HF_HUB_DISABLE_XET'] = '1'
        os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
        from huggingface_hub import HfApi
        token = args.token_file.read_text().strip() if args.token_file else None
        HfApi(token=token).upload_large_folder('HuggingFriends/PixelWorld',
            folder_path=args.destination, repo_type='dataset', num_workers=8)
