"""Lossless image tiling for providers with a per-image byte limit."""
import base64
import hashlib
import io
import json
from PIL import Image

LIMIT = 3_500_000

def split_image(url):
    if not isinstance(url, str) or not url.startswith('data:image/'):
        return None
    header, encoded = url.split(',', 1)
    if ';base64' not in header:
        return None
    data = base64.b64decode(encoded, validate=True)
    if len(data) <= LIMIT:
        return None
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        size = image.size
        # Preserve decoded pixel values and alpha, with no resizing or JPEG loss.
        image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
        pending = [(0, 0, *size)]
        pieces = []
        while pending:
            box = pending.pop()
            crop = image.crop(box)
            output = io.BytesIO()
            crop.save(output, format='PNG')
            content = output.getvalue()
            if len(content) > LIMIT:
                left, top, right, bottom = box
                if right - left >= bottom - top and right - left > 1:
                    middle = (left + right) // 2
                    pending.extend([(left, top, middle, bottom), (middle, top, right, bottom)])
                elif bottom - top > 1:
                    middle = (top + bottom) // 2
                    pending.extend([(left, top, right, middle), (left, middle, right, bottom)])
                else:
                    raise ValueError('Cannot fit lossless input tile in GLM image limit')
                continue
            pieces.append((box, content))
        pieces.sort(key=lambda value: (value[0][1], value[0][0]))
    parts = [{'type': 'text', 'text':
        f'The following {len(pieces)} lossless tiles form one input image of size '
        f'{size[0]}x{size[1]}. All original pixels are retained, with no resizing. '
        'Tile bounds are left, top, right, bottom in the original image.'}]
    evidence = {'original_sha256': hashlib.sha256(data).hexdigest(),
                'original_bytes': len(data), 'dimensions': size, 'tiles': []}
    for number, (box, content) in enumerate(pieces, 1):
        parts.append({'type': 'text', 'text': f'Input-image tile {number}: bounds {box}.'})
        parts.append({'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(content).decode()}})
        evidence['tiles'].append({'bounds': box, 'bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest()})
    return parts, evidence

def lossless_payload(payload):
    evidence = []
    for message in payload['messages']:
        content = []
        for part in message.get('content', []):
            info = None
            if part.get('type') == 'image_url':
                info = split_image(part.get('image_url', {}).get('url'))
            if info is None:
                content.append(part)
            else:
                parts, item = info
                content.extend(parts)
                evidence.append(item)
        message['content'] = content
    if evidence:
        extra = json.loads(payload['extra_info'])
        extra['input_image_transport'] = {'version': 'glm54-lossless-tiles-v1',
                                          'images': evidence}
        payload['extra_info'] = json.dumps(extra)
    return payload
