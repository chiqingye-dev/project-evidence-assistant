"""Fixed lexical classes improve failed-response diagnosis without recovery."""
import json
from unittest.mock import patch

import httpx
import pytest

from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import ModelError
from project_evidence.deepseek_adapter import _json_extra_details
from test_deepseek_adapter import KEY, context, envelope, setup


CASES = [
    (',', 'json.suffix_comma'),
    (':', 'json.suffix_colon'),
    ('"', 'json.suffix_double_quote'),
    ("'", 'json.suffix_single_quote'),
    (r'\n', 'json.suffix_escaped_whitespace'),
    (r'\r', 'json.suffix_escaped_whitespace'),
    (r'\t', 'json.suffix_escaped_whitespace'),
    ('\\', 'json.suffix_backslash'),
    (r'\q', 'json.suffix_backslash'),
    ('\ufeff', 'json.suffix_bom'),
    ('\u00a0', 'json.suffix_nonjson_whitespace'),
    ('\u2003', 'json.suffix_nonjson_whitespace'),
    ('0', 'json.suffix_number_start'),
    ('-1', 'json.suffix_number_start'),
    ('false', 'json.suffix_letter'),
    ('说明', 'json.suffix_letter'),
    ('<', 'json.suffix_symbol'),
    ('+', 'json.suffix_symbol'),
]


@pytest.mark.parametrize('prefix,label', CASES)
@pytest.mark.parametrize('action', ['final', 'tools'])
def test_rejected_suffix_keeps_exact_receipt_and_cannot_dispatch_or_leak(tmp_path, prefix, label, action):
    valid = envelope()['choices'][0]['message']['content']
    if action == 'tools':
        valid = json.dumps(dict(kind='tools', tool_calls=[dict(id='read', name='read_excerpt',
            arguments=dict(path='src/Allowed.java', start_line=1, end_line=1))]))
    content = valid + ' \r\n' + prefix + 'private-suffix-' + KEY
    requests = []
    def respond(request):
        requests.append(1)
        return httpx.Response(200, json=envelope(content))
    adapter, ledger = setup(tmp_path, respond)
    result = run_agent(adapter, lambda *_: pytest.fail('No rejected prefix may dispatch a tool'),
                       lambda: None, lambda: False, initial_context=context())
    logged = result.model_dump_json()
    assert result.status == 'failed' and result.final_output is None
    assert len(requests) == 1 and result.counters.model_calls == 1
    assert result.counters.tool_calls == result.counters.retries == 0
    assert all(name in logged for name in ('action.json_invalid', 'json.extra_data', 'json.extra_other', label))
    assert all(value not in logged for value in (KEY, 'private-suffix-', content))
    assert result.counters.reported_input_tokens == 10000
    assert result.counters.reported_output_tokens == 1000
    summary = ledger.summary('first-trial')
    assert summary['accounted_upper_bound_microyuan'] == 28000 and summary['unresolved_requests'] == 0


@pytest.mark.parametrize('prefix,label', CASES)
def test_classification_is_content_independent_and_never_decodes_a_prefix(prefix, label):
    for private in ('private-A-' + KEY, 'private-B-not-the-same-value'):
        with pytest.raises(json.JSONDecodeError) as error:
            json.loads('{"private":"' + KEY + '"} ' + prefix + private)
        with patch('json.loads', side_effect=AssertionError('No second parse or prefix extraction')):
            assert _json_extra_details(error.value) == ('json.extra_other', label)


@pytest.mark.parametrize('pos', [-1, 100])
def test_invalid_parser_positions_do_not_guess_or_disclose(pos):
    error = json.JSONDecodeError('Extra data', '{}', pos)
    assert _json_extra_details(error) == ()


def test_new_labels_stay_in_exact_whitelist():
    valid = tuple(dict.fromkeys(label for _, label in CASES))
    injected = tuple(label + '.' + KEY for label in valid)
    for label in valid:
        error = ModelError('invalid_response', diagnostics=(label, *injected, KEY))
        assert error.diagnostics == (label,)


@pytest.mark.parametrize('whitespace', [' ', '\n', '\r\n', ' \t\r\n'])
def test_actual_trailing_json_whitespace_remains_accepted(tmp_path, whitespace):
    content = envelope()['choices'][0]['message']['content'] + whitespace
    adapter, ledger = setup(tmp_path, lambda _: httpx.Response(200, json=envelope(content)))
    turn = adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert turn.kind == 'final'
    assert ledger.summary('first-trial')['unresolved_requests'] == 0
