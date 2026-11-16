from datetime import datetime, timedelta, timezone
import gzip
import json

import httpx
import pytest

from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import AgentBudget, ModelContext, SECTION_GOAL
from project_evidence.response_archive import RecordingTransport, ResponseArchive
from project_evidence.simple_section import SimpleSectionAdapter
from project_evidence.spending import SpendingLedger
from project_evidence.storage import ProjectStore
from test_agent_engine import CONTEXT, excerpt
from test_learning_engine import anchor

KEY = "synthetic-key-no-real-credential"


def envelope(name, args):
    return {
        "model": "deepseek-flash", "object": "chat.completion",
        "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call-one", "type": "function", "function": {
                "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def configured(tmp_path, responder):
    ledger = SpendingLedger(ProjectStore(tmp_path / "data"))
    ledger.authorize(confirmed=True, grant_id="fixture", trial_limit=50_000_000,
                     expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    adapter = SimpleSectionAdapter(KEY, ledger, "fixture", transport=httpx.MockTransport(responder))
    return adapter.for_task("simple-task", goal=SECTION_GOAL), ledger


def context():
    return ModelContext(**CONTEXT, goal=SECTION_GOAL, anchor=anchor(True))


@pytest.mark.parametrize("invalid_count", [0, 1, 2, 3])
def test_real_engine_counts_corrections_and_settles_every_response(tmp_path, invalid_count):
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        current = json.loads(payload["messages"][1]["content"])
        if not current["context"]["observations"]:
            value = envelope("read_excerpt", {"path": "src/Example.java", "start_line": 1, "end_line": 1})
        else:
            ids = ["foreign"] if len(sent) <= invalid_count + 1 else ["evidence-1"]
            value = envelope("submit_section", {"blocks": [{"text": "仅说明已读的静态源码。", "evidence_ids": ids}]})
            if len(sent) > 2:
                assert current["previous_validation_errors"]
        return httpx.Response(200, json=value)

    adapter, ledger = configured(tmp_path, respond)
    result = run_agent(adapter, lambda *args: excerpt(), lambda: None, lambda: False,
                       AgentBudget(max_retries=2), initial_context=context())
    assert result.status == ("failed" if invalid_count == 3 else "completed")
    assert result.counters.model_calls == len(sent) == 2 + min(invalid_count, 2)
    assert result.counters.retries == min(invalid_count, 2)
    assert result.counters.reported_input_tokens == 100 * len(sent)
    assert ledger.summary("fixture")["accounted_upper_bound_microyuan"] == 600 * len(sent)
    assert ledger.summary("fixture")["unresolved_requests"] == 0
    records = list((tmp_path / "data/model-responses/simple-task").glob("*.json"))
    assert len(records) == len(sent)
    assert all(KEY not in path.read_text(encoding="utf-8") for path in records)
    if result.status == "completed":
        assert result.final_output.title == context().anchor.section_title
        assert result.final_output.blocks[0].support == "candidate"
        assert adapter.last_claim_locations == [{"field": "block:1:text", "evidence_ids": ["evidence-1"]}]
        assert not result.semantic_verified
    schema = next(tool["function"]["parameters"] for tool in sent[-1]["tools"] if tool["function"]["name"] == "submit_section")
    assert set(schema["properties"]) == {"blocks", "gaps"}


@pytest.mark.parametrize("case", ["protected", "out_of_scope", "unknown_function", "billing_unknown"])
def test_permissions_and_unknown_billing_do_not_trigger_correction(tmp_path, case):
    calls = []

    def respond(request):
        calls.append(1)
        if case == "billing_unknown":
            return httpx.Response(503, text="No receipt")
        path = "docs/M2-NEXT-CHAT-HANDOFF.md" if case == "protected" else "src/Other.java"
        return httpx.Response(200, json=envelope("run_shell" if case == "unknown_function" else "read_excerpt",
            {"path": path, "start_line": 1, "end_line": 1}))

    adapter, ledger = configured(tmp_path, respond)
    adapter._ranges = {"src/Example.java": ((1, 1),)}
    result = run_agent(adapter, lambda *args: pytest.fail("Denied tool executed"), lambda: None, lambda: False,
                       AgentBudget(max_retries=2), initial_context=context())
    assert result.status == "failed" and len(calls) == 1 and result.counters.retries == 0
    assert ledger.summary("fixture")["unresolved_requests"] == int(case == "billing_unknown")


def test_raw_archive_redacts_key_even_in_provider_error_body(tmp_path):
    archive = ResponseArchive(tmp_path, KEY)
    path = archive.save("task", 401, f'{{"error":"Bearer {KEY}"}}'.encode())
    saved = path.read_text(encoding="utf-8")
    assert KEY not in saved and "REDACTED" in saved
    with pytest.raises(ValueError):
        archive.save("../escape", 200, b"x")


def test_compressed_response_is_recorded_and_decoded_once(tmp_path):
    payload = envelope("report_gap", {"message": "缺少源码。", "gaps": ["无法判断实现。"]})
    adapter, ledger = configured(tmp_path, lambda request: httpx.Response(200,
        content=gzip.compress(json.dumps(payload).encode()), headers={"content-encoding": "gzip"}))
    result = run_agent(adapter, lambda *args: None, lambda: None, lambda: False,
                       AgentBudget(max_retries=2), initial_context=context())
    assert result.status == "gap" and ledger.summary("fixture")["unresolved_requests"] == 0


def test_archive_redacts_json_escaped_credentials(tmp_path):
    encoded = "".join("\\u" + format(ord(character), "04x") for character in KEY)
    archive = ResponseArchive(tmp_path, KEY)
    path = archive.save("task", 401, ('{"error":"' + encoded + '"}').encode())
    body = json.loads(path.read_text(encoding="utf-8"))["response_body"]
    assert KEY not in json.loads(body)["error"] and "REDACTED" in body


def test_owned_network_transport_is_closed_after_each_request(tmp_path):
    closed = []

    class OwnedTransport(httpx.MockTransport):
        def close(self):
            closed.append(True)

    transport = RecordingTransport(None, ResponseArchive(tmp_path, KEY), "task",
        transport_factory=lambda: OwnedTransport(lambda request: httpx.Response(200, json={"ok": True})))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://example.com").json() == {"ok": True}
        assert client.get("https://example.com").json() == {"ok": True}
    assert len(closed) == 2


def test_baseline_request_does_not_inherit_legacy_read_instructions(tmp_path):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=envelope("report_gap", {"message": "缺证据", "gaps": ["未知"]}))

    adapter, _ = configured(tmp_path, respond)
    adapter.allow_source_tools = False
    adapter._ranges = {"src/Example.java": ((35, 56),)}
    run_agent(adapter, lambda *args: None, lambda: None, lambda: False, initial_context=context())
    body = captured[0]
    assert "不提供源码工具" in body["messages"][0]["content"]
    assert "先按需使用" not in body["messages"][0]["content"]
    payload = json.loads(body["messages"][1]["content"])
    assert "system_instruction" not in payload["context"]
    assert payload["allowed_source_ranges"] == {"src/Example.java": [[35, 56]]}
    assert {tool["function"]["name"] for tool in body["tools"]} == {"report_gap"}
