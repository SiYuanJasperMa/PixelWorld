"""Canonical public entry points; no host paths or credentials."""
import re

CODE_REPOSITORY = 'https://github.com/SiYuanJasperMa/PixelWorld'
HF_DATASET = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld'
HF_SPACE = 'https://huggingface.co/spaces/HuggingFriends/PixelWorld-dashboard'
DASHBOARD = 'https://huggingfriends-pixelworld-dashboard.static.hf.space/index.html'

_OLD_CODE = re.compile(r'https?://github\.com/siyuanmacs/pixelworld(?=$|[/#?\s\"\'<>)])', re.I)


def canonicalize_code_links(content):
    """Keep cached legacy page templates from reintroducing the old code URL."""
    return _OLD_CODE.sub(CODE_REPOSITORY, content)
