"""Opt-in replay compaction of old tool pairs; never alters task or image inputs.

This is an experimental transport transformation. Original calls and outputs
must be archived inside the same case before sending any shortened request.
"""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import re

ELIGIBLE = {'exec_command', 'write_stdin', 'apply_patch'}
PROTECTED_INPUTS = ('prompt.txt','GENERATION_RULES.md','task_spec.json','first_frame.png',
                    'source_info.json','source_pixels.npz','conditioning_info.json')


def compact(items, keep_recent=4, archive_dir=None, keep_generated_images=None,
            retain_tool_calls=False):
    if keep_recent < 1:
        raise ValueError('Keep at least one recent complete tool interaction')
    calls = {}
    pairs = []
    for index, item in enumerate(items):
        if item.get('type') in ('function_call', 'custom_tool_call'):
            calls[item.get('call_id')] = (index, item)
        elif item.get('type') in ('function_call_output', 'custom_tool_call_output'):
            call = calls.get(item.get('call_id'))
            if call:
                pairs.append((call[0], index, call[1], item))
    def input_discovery(call):
        text=call.get('arguments') or call.get('input') or ''
        return any(name in text for name in PROTECTED_INPUTS)
    candidates = [pair for pair in pairs[:-keep_recent]
                  if pair[2].get('name') in ELIGIBLE and isinstance(pair[3].get('output'),str)
                  and not input_discovery(pair[2])]
    def generated_inspection(call):
        if call.get('name') != 'view_image':return False
        try:path=json.loads(call.get('arguments')or'{}').get('path','')
        except ValueError:return False
        return Path(path).name in ('inspection.png','transition_check.png')
    image_pairs=[p for p in pairs if generated_inspection(p[2])]
    if keep_generated_images is not None:
        if keep_generated_images<1:raise ValueError('Keep at least one generated inspection')
        candidates += image_pairs[:-keep_generated_images]
    replaced = {}
    removed = set()
    archived = []
    for call_index, output_index, call, output in candidates:
        if call_index in replaced:continue
        image_mode=keep_generated_images is not None and generated_inspection(call)
        if not image_mode and (call.get('name') not in ELIGIBLE or not isinstance(output.get('output'), str)):
            continue
        original = {'call': deepcopy(call), 'output': deepcopy(output)}
        encoded = json.dumps(original, ensure_ascii=False, separators=(',', ':')).encode()
        identity = hashlib.sha256(encoded).hexdigest()
        relative = '.tmp/harness-history/' + identity[:20] + '.json'
        if archive_dir is not None:
            target = Path(archive_dir) / (identity[:20] + '.json')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(encoded)
        image_paths=[]
        if image_mode and isinstance(output.get('output'),list):
            for block in output['output']:
                url=block.get('image_url','')
                if not isinstance(url,str) or not url.startswith('data:image/'):continue
                header,body=url.split(',',1)
                raw=base64.b64decode(body,validate=True)
                suffix='.jpg' if 'jpeg' in header else '.png' if 'png' in header else '.webp'
                filename=identity[:20]+'-'+str(len(image_paths))+suffix
                if archive_dir is not None:(Path(archive_dir)/filename).write_bytes(raw)
                image_paths.append('.tmp/harness-history/'+filename)
        result = output.get('output', '')
        status = re.search(r'Process exited with code (\d+)', result) if isinstance(result,str) else None
        outcome = 'exit=' + status.group(1) if status else 'completed tool result'
        receipt = ('[Harness history receipt: earlier ' + call['name'] + ', ' + outcome + '. '
                   'Full original call and result archived at ' + relative + '. '
                   'Current source and saved RGB artifacts remain in the workspace. '
                   'Read the archive if earlier details are needed.'+
                   (' Archived generated inspection: '+', '.join(image_paths)+'.' if image_paths else '')+']')
        if retain_tool_calls:
            # Keep the exact executable code and call identity in history.
            # Only its old return value is replaced by a retrievable receipt.
            replaced[output_index] = {**deepcopy(output), 'output':receipt}
        else:
            replaced[call_index] = {'type': 'message', 'role': 'assistant',
                                   'content': [{'type': 'output_text', 'text': receipt}]}
            removed.add(output_index)
        archived.append({'relative_path': relative, 'sha256': identity, 'original': original,
                         'image_paths':image_paths})
    result = [deepcopy(replaced.get(index, item)) for index, item in enumerate(items) if index not in removed]
    before = len(json.dumps(items, ensure_ascii=False).encode())
    after = len(json.dumps(result, ensure_ascii=False).encode())
    return result, {'tool_pairs_compacted': len(archived), 'serialized_bytes_before': before,
                    'serialized_bytes_after': after, 'saved_bytes': before-after,
                    'archived': archived, 'quality_validated': False,
                    'retain_tool_calls':retain_tool_calls}
