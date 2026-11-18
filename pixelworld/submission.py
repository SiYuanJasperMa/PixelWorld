"""Case-bound MCP submission checks; never generates pixels or evaluates physics."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

from .audit import validate, canonicalize_tensor
from .media import contract
from .util import atomic_json, sha256, utc_now


TOOL = {
    'name': 'submit_video',
    'description': ('Submit the current saved RGB video when satisfied with its appearance and motion. '
                    'Checks the complete technical output contract and workspace policy, without judging physics '
                    'or generating pixels. Fix reported errors and retry, or finish after accepted=true.'),
    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'annotations': {'readOnlyHint': False, 'destructiveHint': False,
                    'idempotentHint': True, 'openWorldHint': False},
}


def safe_outputs(workspace, spec):
    """A privileged checker may only follow output paths within its fixed case."""
    names = ['generate.py', 'pixels.npz', 'inspection.png', 'transition_check.png',
             'index.html', 'frames']
    if spec.get('audio'):
        names.append('audio.wav')
    for name in names:
        path = workspace / name
        if not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('Output path leaves the case: ' + name)
    for path in (workspace / 'frames').glob('*.png'):
        if not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('Frame path leaves the case')
    tensor = workspace / 'pixels.npz'
    if tensor.is_file():
        with zipfile.ZipFile(tensor) as archive:
            entries = archive.infolist()
            limit = spec['width'] * spec['height'] * spec['frames'] * 4 + 1024**2
            if len(entries) != 1 or entries[0].filename != 'pixels.npy' or entries[0].file_size > limit:
                raise ValueError('Unexpected or oversized packed RGB tensor')


def submit(run):
    run = run.resolve()
    manifest = json.loads((run / 'run_manifest.json').read_text())
    if manifest.get('harness_variant') not in ('flash-submit','flash-submit-boundary') or manifest.get('filesystem_policy') != 'case-only-v1':
        raise ValueError('Submission tool requires the explicit isolated experimental variant')
    workspace, logs = run / 'workspace', run / 'logs'
    spec = contract(run / 'media_contract.json')
    safe_outputs(workspace, spec)
    canonicalization = canonicalize_tensor(workspace, logs, spec)
    report = validate(workspace, logs / 'events.jsonl', -1, manifest['track'],
                      require_reports=False, media=spec, submission_requested=True)
    reasons = []
    if report['missing_required']:
        reasons.append('Missing required output files: ' + ', '.join(report['missing_required']))
    if report['frames'] != spec['frames']:
        reasons.append('Incorrect frame count')
    if not report['png_tensor_exact']:
        reasons.append('RGB tensor and PNG frames must match the declared shape and dtype exactly')
    if spec['task_type'] == 'i2v' and not report['first_frame_constraint_satisfied']:
        reasons.append('Output frame zero violates the input-frame constraint')
    if report['audio_contract_exact'] is False:
        reasons.append('Audio does not match the media contract')
    if report['generate_runs'] < 1:
        reasons.append('Create and run generate.py before submitting')
    if any(report[key] for key in ('outside_workspace_reads', 'outside_workspace_file_changes',
                                   'network_commands', 'subagent_commands', 'outside_workspace_command_paths')):
        reasons.append('Recorded execution violates workspace or tool policy')
    if report['track_violation']:
        reasons.append('The track disables visual output tools')
    accepted = bool(report['pass'] and not reasons)
    receipt = {'schema': 'pixelworld-submission-request-v1', 'utc': utc_now(),
               'accepted': accepted, 'errors': reasons, 'physics_score_provided': False,
               'pixels_generated': False, 'validation': report,
               'canonicalization': canonicalization}
    if accepted:
        receipt['artifact_sha256'] = {name: sha256(workspace / name) for name in
            ('generate.py', 'pixels.npz', 'inspection.png', 'transition_check.png', 'index.html')}
    atomic_json(logs / 'submission_receipt.json', receipt)
    return {'accepted': accepted, 'errors': reasons, 'physics_score_provided': False,
            'frames': spec['frames'], 'fps': spec['fps']}


def handle(request, run):
    method = request.get('method')
    if method == 'initialize':
        return {'protocolVersion': request.get('params', {}).get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}}, 'serverInfo': {'name': 'pixelworld-submit', 'version': '0.1'}}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method == 'tools/call':
        params = request.get('params', {})
        if params.get('name') != 'submit_video' or params.get('arguments', {}) != {}:
            raise ValueError('submit_video accepts no file paths or arguments')
        try:
            result = submit(run)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            result = {'accepted': False, 'errors': [str(error)], 'physics_score_provided': False}
        return {'content': [{'type': 'text', 'text': json.dumps(result)}], 'isError': not result['accepted']}
    if method == 'ping':
        return {}
    raise ValueError('Unsupported MCP method')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        try:
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': handle(request, args.run)}
        except ValueError as error:
            response = {'jsonrpc': '2.0', 'id': request['id'],
                        'error': {'code': -32602, 'message': str(error)}}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
