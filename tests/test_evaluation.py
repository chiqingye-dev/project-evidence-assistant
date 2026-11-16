from types import SimpleNamespace

from project_evidence.evaluation import concept_check, load_suite, retrieve_reads, summarize


def test_public_suite_is_small_fixed_and_excludes_retired_s03():
    from pathlib import Path

    suite = load_suite(Path(__file__).resolve().parents[1] / "evaluation/fixed_v05.json")
    assert len(suite["cases"]) == 10
    assert {case["topic"] for case in suite["cases"]} == {"query", "submission", "consumption", "judging"}
    assert sum(case.get("expected_gap", False) for case in suite["cases"]) == 2
    assert not any(case["id"] == "M2-S03" for case in suite["cases"])


def test_retrieval_ranks_only_allowed_chunks_and_ties_are_stable():
    case = {"keywords": ["target"], "sources": [
        {"path": "A.java", "start_line": 1, "end_line": 2},
        {"path": "B.java", "start_line": 3, "end_line": 4},
        {"path": "C.java", "start_line": 5, "end_line": 6},
    ]}
    texts = {"A.java": "target", "B.java": "target target", "C.java": "none"}
    tools = SimpleNamespace(read_excerpt=lambda **span: {"status": "ok", "truncated": False, "text": texts[span["path"]]})
    reads = retrieve_reads(case, tools)
    assert [row["arguments"]["path"] for row in reads] == ["B.java", "A.java"]
    assert all(row["name"] == "read_excerpt" for row in reads)


def test_concept_score_does_not_label_itself_semantic_or_independent_review():
    case = {"concepts": [["ACTIVE"], ["PUBLIC"]]}
    result = {"status": "completed", "final_output": {"blocks": [{"text": "ACTIVE PUBLIC"}]}}
    checks = concept_check(case, result)
    assert checks["concept_check_passed"]
    assert checks["semantic_review"] == "not_independently_reviewed"
    result["status"] = "failed"
    assert not concept_check(case, result)["concept_check_passed"]


def test_summary_counts_failures_and_separates_uncorrected_from_corrected():
    suite = {"cases": [{"id": "Q1", "topic": "query"}]}
    records = [{
        "case_id": "Q1", "method": "agent", "validation_failures": failures,
        "checks": {"protocol_passed": passed, "concept_check_passed": passed},
        "spending": {"fee_microyuan": 600, "provider_calls": 1}, "elapsed_seconds": 1.0,
    } for failures, passed in [(0, True), (1, True), (3, False)]]
    row = summarize(records, suite)[0]
    assert row["runs"] == 3 and row["first_format_passes"] == 1
    assert row["format_passes_after_correction"] == 2 and row["concept_check_passes"] == 2
    assert row["fee_microyuan"] == 1800
