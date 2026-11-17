"""Stage the pinned Codex helper from its own distribution, not a system bwrap."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PINS = {'0.169.0': '15fd5f2673d2e1c2f80e34e5108462fd014f5e8bbc7b0731f1ab23d1ff1ac140'}


def stage(source_codex, destination, version='0.169.0'):
    source_codex = source_codex.resolve()
    package = source_codex.parent.parent
    descriptor = json.loads((package / 'codex-package.json').read_text())
    if descriptor['version'] != version or version not in PINS:
        raise ValueError('Use the explicitly pinned Codex distribution')
    resource_dir = Path(descriptor['resourcesDir'])
    if resource_dir.is_absolute() or '..' in resource_dir.parts:
        raise ValueError('Unexpected resource directory')
    source = package / resource_dir / 'bwrap'
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != PINS[version]:
        raise ValueError('Bundled helper does not match the pinned release hash')
    target = destination / 'bwrap'
    temporary = destination / 'bwrap.staging'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, temporary)
    temporary.replace(target)
    return {'codex_version': version, 'bwrap_sha256': digest,
            'bytes': target.stat().st_size, 'source': 'same-version codex-resources/bwrap'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-codex', type=Path, required=True)
    parser.add_argument('--destination-bin', type=Path, required=True)
    parser.add_argument('--version', default='0.169.0')
    args = parser.parse_args()
    print(json.dumps(stage(args.source_codex, args.destination_bin, args.version)))
