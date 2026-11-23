"""Strict model-route translation for a controlled native Messages client.

The transport caller retains both wire representations. Only ordinary text,
embedded image, and function-tool content are supported. Unsupported native
features are rejected rather than silently changing the experiment.
"""
from __future__ import annotations

import base64
import json
import re

_ID = re.compile(r'[A-Za-z0-9_-]+$')
_IMAGE = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def _name(value):
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise ValueError('Invalid tool name or call identity')
    return value


def _parts(content):
    if isinstance(content,str):
        return [{'type':'input_text','text':content}]
    parts=[]
    for b in content:
        if b['type']=='text':
            if not isinstance(b['text'],str):raise ValueError('Invalid text block')
            parts.append({'type':'input_text','text':b['text']})
        elif b['type']=='image':
            s=b['source']
            if s.get('type')!='base64' or s.get('media_type') not in _IMAGE:
                raise ValueError('Images must be embedded, not retrieved through the adapter')
            raw=base64.b64decode(s['data'],validate=True)
            if not raw:raise ValueError('Empty embedded image')
            parts.append({'type':'input_image','image_url':f"data:{s['media_type']};base64,{s['data']}"})
        else:
            raise ValueError('Unsupported content block: '+str(b['type']))
    return parts


def to_responses(payload: dict, *, model: str, reasoning_effort: str) -> dict:
    if payload.get('model') != model:
        raise ValueError('Client requested a different model from the pinned route')
    if type(payload.get('max_tokens')) is not int or payload['max_tokens']<1:
        raise ValueError('A positive native max_tokens is required')
    # The pinned native client asks for adaptive thinking with this effort.
    # Its no-op "keep all" directive applies to a native thinking block,
    # which this transport does not synthesize from provider reasoning.
    thinking = payload.get('thinking')
    if thinking is not None and thinking not in (
        {'type': 'adaptive', 'display': 'omitted'},
        {'type': 'adaptive', 'display': 'updates'},
    ):
        raise ValueError('Unsupported native thinking configuration')
    if payload.get('output_config') not in (None, {'effort': reasoning_effort}):
        raise ValueError('Native effort differs from the locked effort')
    if payload.get('context_management') not in (
        None, {'edits': [{'type': 'clear_thinking_20251015', 'keep': 'all'}]},
    ):
        raise ValueError('Unsupported native context management')
    for field in ('stop_sequences', 'top_k', 'top_p', 'temperature'):
        if payload.get(field) is not None:
            raise ValueError(f'Native {field} cannot be represented by this locked route')
    items=[]
    system=payload.get('system')
    if system:
        parts=_parts(system)
        if any(x['type']!='input_text' for x in parts):raise ValueError('Invalid system content')
        items.append({'role':'system','content':parts})
    seen=set()
    for message in payload['messages']:
        role=message['role']
        if role not in ('assistant','user'):raise ValueError('Unsupported message role')
        content=message['content']; content=[{'type':'text','text':content}] if isinstance(content,str) else content
        parts=[]
        def flush():
            if parts:
                if role == 'assistant':
                    if any(p['type'] != 'input_text' for p in parts):
                        raise ValueError('Unsupported assistant content')
                    items.append({'role': role, 'content': '\n'.join(p['text'] for p in parts)})
                else:
                    items.append({'role': role, 'content': parts.copy()})
                parts.clear()
        for block in content:
            kind=block['type']
            if kind=='tool_use':
                if role!='assistant' or not isinstance(block['input'],dict):raise ValueError('Invalid tool call')
                flush(); cid=_name(block['id'])
                if cid in seen:raise ValueError('Duplicate tool call identity')
                seen.add(cid)
                items.append({'type':'function_call','call_id':cid,'name':_name(block['name']),
                              'arguments':json.dumps(block['input'],separators=(',',':'))})
            elif kind=='tool_result':
                if role!='user' or block['tool_use_id'] not in seen:raise ValueError('Unpaired tool result')
                flush();data=_parts(block.get('content',''))
                if block.get('is_error'):data.insert(0,{'type':'input_text','text':'Tool returned an error.'})
                items.append({'type':'function_call_output','call_id':block['tool_use_id'],'output':data})
            else:
                parts.extend(_parts([block]))
        flush()
    result={'model':model,'input':items,'max_output_tokens':payload['max_tokens'],
            'reasoning':{'effort':reasoning_effort}}
    if payload.get('tools'):
        result['tools']=[{'type':'function','name':_name(t['name']), 'description':t.get('description',''),
                          'parameters':t['input_schema']} for t in payload['tools']]
    choice=payload.get('tool_choice')
    if choice:
        strategy=choice['type']
        if strategy not in ('any','auto','none','tool'):raise ValueError('Unsupported tool choice')
        result['tool_choice']=('required' if strategy=='any' else strategy) if strategy!='tool' else {'type':'function','name':_name(choice['name'])}
        if choice.get('disable_parallel_tool_use') is True:result['parallel_tool_calls']=False
    return result


def to_messages(reply: dict, *, model: str) -> dict:
    if reply.get('status') not in ('completed','incomplete'):
        raise ValueError('Provider response did not return a usable terminal result')
    content=[]
    for item in reply.get('output',[]):
        if item['type']=='message':
            for part in item['content']:
                if part['type']!='output_text':raise ValueError('Unsupported provider output block')
                content.append({'type':'text','text':part['text']})
        elif item['type']=='function_call':
            data=json.loads(item['arguments'])
            if not isinstance(data,dict):raise ValueError('Invalid provider tool arguments')
            content.append({'type':'tool_use','id':_name(item['call_id']),'name':_name(item['name']),'input':data})
        elif item['type'] != 'reasoning':
            raise ValueError('Unsupported provider output item')
        # Provider reasoning is kept in the raw response by the caller, not
        # turned into a Claude thinking block with an invented signature.
    u=reply.get('usage') or {}
    usage={k:u[k] for k in ('input_tokens','output_tokens')}
    if any(type(v) is not int or v<0 for v in usage.values()):raise ValueError('Invalid provider token usage')
    stop='max_tokens' if reply['status']=='incomplete' else 'tool_use' if any(x['type']=='tool_use'for x in content) else 'end_turn'
    return {'id':reply['id'],'type':'message','role':'assistant','model':model,'content':content,
            'stop_reason':stop,'stop_sequence':None,'usage':usage}
