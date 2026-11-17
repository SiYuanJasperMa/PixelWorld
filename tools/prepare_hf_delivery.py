"""Freeze public previews and complete sanitized traces without modifying live data."""
import argparse
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
        return {key: ('[redacted]' if key.lower() in
            {'authorization', 'password', 'api_key', 'access_token', 'secret_key'}
            else sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'\bhf_[A-Za-z0-9]{20,}', '[redacted]', public_text(value))
    return value


def json_copy(source, target):
    data = sanitize(json.loads(source.read_text()))
    if isinstance(data, dict) and 'case_id' in data and 'candidates' in data:
        data['first_frame'] = None
        data['source_video'] = None
        for candidate in data['candidates']:
            for key in ('pixels', 'inspection', 'transition_check'):
                candidate[key] = None
            candidate.pop('chunks', None)
        for event in data.get('events', []):
            event.pop('images', None)
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


TRACE_FILES = ('run_manifest.json', 'media_contract.json', 'task_prompt.txt',
    'input_snapshot/prompt.txt', 'logs/events.jsonl', 'logs/events.raw.jsonl',
    'logs/token_budget.json', 'logs/full_rollout.jsonl', 'workspace/generate.py',
    'workspace/GENERATION_RULES.md', 'workspace/artifact_io.py',
    'workspace/inspection.png', 'workspace/transition_check.png')


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
                    for name in TRACE_FILES:
                        path = run / name
                        if not path.is_file():
                            continue
                        if path.suffix == '.png':
                            content = path.read_bytes()
                        else:
                            text = path.read_text(errors='replace')
                            if path.suffix == '.json':
                                content = json.dumps(sanitize(json.loads(text)), ensure_ascii=False).encode()
                            elif path.suffix == '.jsonl':
                                lines = []
                                for line in text.splitlines():
                                    try:
                                        lines.append(json.dumps(sanitize(json.loads(line)), ensure_ascii=False))
                                    except ValueError:
                                        lines.append(sanitize(line))
                                content = ('\n'.join(lines) + '\n').encode()
                            else:
                                content = sanitize(text).encode()
                        info = tarfile.TarInfo(prefix + '/' + name)
                        info.size = len(content)
                        info.mode = 0o644
                        archive.addfile(info, io.BytesIO(content))
            item = {'benchmark': record['benchmark'], 'model': model, 'attempt_records': count,
                    'archive': str(target.relative_to(destination)), 'bytes': target.stat().st_size}
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
