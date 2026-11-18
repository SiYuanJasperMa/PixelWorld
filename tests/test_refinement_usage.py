import json
import pytest
from tools.analyze_refinement_usage import measured_usage


def test_usage_boundary_deduplicates_and_requires_terminal_agreement(tmp_path):
    path = tmp_path / 'trace.jsonl'
    def event(time, cumulative, last):
        return {'timestamp': time, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': cumulative, 'last_token_usage': last}}}
    first = event('2026-11-18T00:00:01Z', {'input_tokens': 100, 'output_tokens': 10},
                  {'input_tokens': 100, 'output_tokens': 10})
    second = event('2026-11-18T00:00:03Z', {'input_tokens': 300, 'output_tokens': 30},
                   {'input_tokens': 200, 'output_tokens': 20})
    path.write_text('\n'.join(json.dumps(x) for x in [first, first, second, second]))
    result = measured_usage(path, '2026-11-18T00:00:02+00:00', {'input_tokens': 300, 'output_tokens': 30})
    assert result['requests'] == 2 and result['requests_reported_after'] == 1
    assert result['input_reported_after'] == 200 and result['output_reported_after'] == 20
    with pytest.raises(ValueError, match='terminal'):
        measured_usage(path, '2026-11-18T00:00:02Z', {'input_tokens': 400, 'output_tokens': 30})
    with pytest.raises(ValueError, match='timezone'):
        measured_usage(path, '2026-11-18T00:00:02', {'input_tokens': 300, 'output_tokens': 30})
