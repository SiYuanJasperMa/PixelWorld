import pytest

from pixelworld.provider_completion import native_output_limit, terminal_status


@pytest.mark.parametrize('reason', ['length', 'max_tokens', 'max_output_tokens'])
def test_truncation_is_incomplete_even_if_http_succeeded(reason):
    assert terminal_status(reason) == ('incomplete', {'reason': 'max_output_tokens'})


@pytest.mark.parametrize('reason', ['stop', 'tool_calls', 'function_call'])
def test_normal_answer_and_tool_calls_complete(reason):
    assert terminal_status(reason) == ('completed', None)


def test_unknown_termination_cannot_be_silently_accepted():
    with pytest.raises(ValueError):
        terminal_status(None)
    assert terminal_status('content_filter') == ('incomplete', {'reason': 'content_filter'})


def test_configured_limit_does_not_replace_explicit_client_limit():
    assert native_output_limit({}, 128000) == 128000
    assert native_output_limit({'max_output_tokens': 1024}, 128000) == 1024


@pytest.mark.parametrize('value', [None, 0, -1, True, '128000'])
def test_invalid_limit_is_rejected(value):
    with pytest.raises(ValueError):
        native_output_limit({'max_output_tokens': value})
