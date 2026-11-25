"""Preserve upstream stop semantics when adapting a Responses provider."""
from __future__ import annotations


def terminal_status(finish_reason: str | None) -> tuple[str, dict | None]:
    """A length-limited upstream response is never a completed agent answer."""
    if finish_reason in ('length', 'max_tokens', 'max_output_tokens'):
        return 'incomplete', {'reason': 'max_output_tokens'}
    if finish_reason in ('content_filter', 'filter'):
        return 'incomplete', {'reason': 'content_filter'}
    if finish_reason in ('stop', 'tool_calls', 'function_call'):
        return 'completed', None
    raise ValueError(f'Unsupported upstream finish reason: {finish_reason!r}')


def native_output_limit(request: dict, default: int = 32000) -> int:
    """Keep an explicit client limit; validate the configured fallback."""
    value = request.get('max_output_tokens', default)
    if type(value) is not int or value < 1:
        raise ValueError('max_output_tokens must be a positive integer')
    return value
