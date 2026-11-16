"""Repeated development benchmark with a same-model retrieval baseline.

Expected concepts are grader data and never enter generation requests. Raw
responses and individual failures stay local; only aggregate results are public.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from time import perf_counter
from uuid import uuid4

import httpx

from .agent_engine import run_agent
from .agent_protocol import AgentBudget, ModelContext, SECTION_GOAL
from .development_config import read_api_key
from .public_verify import PublicGitHubVerifier
from .simple_section import SimpleSectionAdapter
from .source_policy import SourceError, normalize_github_repository, require_allowed_path
from .source_ranges import RangedSourceTools, normalize_read_ranges
from .source_service import SourceService
from .spending import SpendingLedger
from .storage import ProjectStore


GRANT_ID = "deepseek-flash-2026-10-13-initial"
DENIED_SOURCE = "forgeoj-judge-worker/src/main/java/com/forgeoj/worker/task/JudgeTaskMapper.java"


def load_suite(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = value["cases"]
    if not 8 <= len(cases) <= 12 or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("A fixed suite requires 8–12 distinct cases")
    if value.get("repetitions") != 3:
        raise ValueError("This benchmark requires three repeats per case")
    for case in cases:
        if case["id"] == "M2-S03" or case["title"] == "事务写入边界":
            raise ValueError("S03 optimisation is retired")
        for span in case["sources"]:
            require_allowed_path(span["path"])
            if span["path"] == DENIED_SOURCE:
                raise ValueError("The previously denied Mapper is excluded")
        ranges_for(case)
    return value


def ranges_for(case):
    ranges = {}
    for span in case["sources"]:
        ranges.setdefault(span["path"], []).append((span["start_line"], span["end_line"]))
    return normalize_read_ranges(ranges)


def concept_check(case, result):
    output = result.get("final_output") or {}
    text = json.dumps(output, ensure_ascii=False)
    accepted_status = {"completed", "gap"} if case.get("expected_gap") else {"completed"}
    matches = [any(term.casefold() in text.casefold() for term in alternatives)
               for alternatives in case["concepts"]]
    return {
        "protocol_passed": result["status"] in accepted_status,
        "concept_matches": matches,
        "concept_check_passed": result["status"] in accepted_status and all(matches),
        "semantic_review": "not_independently_reviewed",
    }


def retrieve_reads(case, tools, top_k=2):
    scored = []
    for index, span in enumerate(case["sources"]):
        payload = tools.read_excerpt(**span)
        if payload.get("status") != "ok" or payload.get("truncated"):
            raise SourceError("denied", "The retrieval pool contains an unavailable source chunk")
        text = payload.get("excerpt", payload.get("text", "")).casefold()
        score = sum(text.count(word.casefold()) for word in case["keywords"])
        scored.append((score, index, span))
    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:top_k]
    return [{"id": f"retrieval-{index}", "name": "read_excerpt", "arguments": span}
            for index, (_, _, span) in enumerate(selected, 1)]


def spending_for_task(projects, task_id):
    with projects._connection() as database:
        rows = database.execute(
            "SELECT state,charged,input_tokens,output_tokens FROM model_spend WHERE task_id=?",
            (task_id,),
        ).fetchall()
    return {
        "provider_calls": len(rows),
        "fee_microyuan": sum(row["charged"] or 0 for row in rows),
        "unresolved_requests": sum(row["state"] != "settled" for row in rows),
        "input_tokens": sum(row["input_tokens"] or 0 for row in rows),
        "output_tokens": sum(row["output_tokens"] or 0 for row in rows),
    }


def run_case(case, method, repetition, *, source, guard, projects, record, ledger, key,
             proxy_port, batch_id, transport=None):
    task_id = f"eval-{batch_id}-{case['id']}-{method}-{repetition}"
    tools = RangedSourceTools(source, ranges_for(case))
    adapter = SimpleSectionAdapter(
        key, ledger, GRANT_ID, proxy_port=proxy_port, transport=transport,
        read_ranges=ranges_for(case),
    ).for_task(task_id, goal=SECTION_GOAL, topic=case["topic"])
    started = perf_counter()
    if method == "retrieval":
        adapter.initial_reads = retrieve_reads(case, tools)
        adapter.allow_source_tools = False
    retrieval_ms = round((perf_counter() - started) * 1000, 2)

    def execute(name, arguments):
        guard()
        result = getattr(tools, name)(**arguments)
        guard()
        if name == "read_excerpt" and result.get("status") == "ok" and result.get("text"):
            result["evidence_id"] = projects.save_evidence(record.id, result)["id"]
        return result

    anchor = {
        "directory_task_id": "evaluation", "chapter_index": 0, "topic": case["topic"],
        "title": case["title"], "summary": case["question"],
        "entry_hints": list(ranges_for(case)), "outline_id": "evaluation",
        "section_id": case["id"], "section_title": case["title"],
        "section_objective": case["question"],
    }
    context = ModelContext(commit=source.commit, public_url=source.public_url,
                           goal=SECTION_GOAL, anchor=anchor)
    outcome = run_agent(adapter, execute, guard, lambda: False,
                        AgentBudget(max_retries=2), initial_context=context)
    adapter._key = ""
    payload = outcome.model_dump(mode="json")
    spending = spending_for_task(projects, task_id)
    result = {
        "task_id": task_id, "case_id": case["id"], "topic": case["topic"],
        "method": method, "repetition": repetition,
        "validation_failures": adapter.validation_failures,
        "elapsed_seconds": round(perf_counter() - started, 3), "retrieval_ms": retrieval_ms,
        "spending": spending, "claim_locations": adapter.last_claim_locations,
        "outcome": payload,
    }
    result["checks"] = concept_check(case, payload)
    return result


def summarize(records, suite):
    rows = []
    for case in suite["cases"]:
        for method in ("agent", "retrieval"):
            runs = [row for row in records if row["case_id"] == case["id"] and row["method"] == method]
            rows.append({
                "case_id": case["id"], "topic": case["topic"], "method": method,
                "runs": len(runs),
                "first_format_passes": sum(row["checks"]["protocol_passed"] and row["validation_failures"] == 0 for row in runs),
                "format_passes_after_correction": sum(row["checks"]["protocol_passed"] for row in runs),
                "concept_check_passes": sum(row["checks"]["concept_check_passed"] for row in runs),
                "fee_microyuan": sum(row["spending"]["fee_microyuan"] for row in runs),
                "provider_calls": sum(row["spending"]["provider_calls"] for row in runs),
                "mean_seconds": round(sum(row["elapsed_seconds"] for row in runs) / len(runs), 3) if runs else None,
            })
    return rows


def write_report(path, summary, suite, *, complete):
    lines = [
        "# 0.5固定开发评测", "",
        f"状态：{'完成' if complete else '未完成'}；10题×3次×2方法；固定源码 `{suite['commit']}`。",
        "同一个deepseek-flash模型、相同正文Schema和任务预算。Agent选择只读工具；基线按关键词取top 2片段后生成。",
        "要点检查是固定词组覆盖检查，不能单独证明语义正确；开发者源码辅助复核也不是独立盲评。题目属于已知项目的开发评测，不是未见留出。", "",
        "|题目|方法|次数|无纠正格式通过|纠正后格式通过|要点检查通过|费用（元）|平均秒|",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        n = row["runs"]
        seconds = row["mean_seconds"] if n else "—"
        lines.append(f"|{row['case_id']}|{row['method']}|{n}|{row['first_format_passes']}/{n}|"
                     f"{row['format_passes_after_correction']}/{n}|{row['concept_check_passes']}/{n}|"
                     f"{row['fee_microyuan'] / 1_000_000:.6f}|{seconds}|")
    lines.extend(["", "每题只有3次，不能据此声称稳定通用成功率。S03单题攻关已停止，其历史失败保留为限制案例。",
                  "原始响应、每次结果、纠正和可信usage在本机忽略提交的data目录中；报告不会发布API Key。", ""])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Fixed 0.5 development benchmark")
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--suite", type=Path, default=root / "evaluation/fixed_v05.json")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--proxy-port", type=int, default=0)
    parser.add_argument("--output", type=Path, default=root / "docs/evaluation-v05.md")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    suite = load_suite(args.suite)
    if args.prepare_only:
        print(json.dumps({"cases": len(suite["cases"]), "repetitions": 3, "methods": 2,
                          "maximum_runs": 60, "model_calls": 0}, ensure_ascii=False))
        return
    key = read_api_key()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is not configured; no requests sent")
    projects = ProjectStore(args.data_dir)
    ledger = SpendingLedger(projects)
    initial = ledger.summary(GRANT_ID)
    if initial["status"] != "ready" or initial["unresolved_requests"]:
        raise SystemExit("Existing budget/price/receipts must be ready; no requests sent")
    record = next((item for item in projects.list()
                   if item.commit == suite["commit"]
                   and normalize_github_repository(item.public_url) == normalize_github_repository(suite["source_url"])
                   and Path(item.source_path).resolve() == args.source.resolve()), None)
    if record is None:
        raise SystemExit("Register the fixed public source in the existing project first")
    transport = httpx.HTTPTransport(proxy=f"http://127.0.0.1:{args.proxy_port}" if args.proxy_port else None,
                                    trust_env=False, retries=0)
    sources = SourceService(projects, PublicGitHubVerifier(transport, prefer_html=True))
    sources.verify(record)
    identity = sources.session_identity(record)
    source = sources.source(record)

    def guard():
        sources.require_session(record, identity)
        source.entries()  # Revalidate the immutable local Git-object boundary.

    directory = args.resume or args.data_dir / "evaluations" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=bool(args.resume))
    results_path = directory / "results.jsonl"
    records = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()] if results_path.exists() else []
    completed = {(row["case_id"], row["method"], row["repetition"]) for row in records}
    batch_id = directory.name.replace("-", "")
    stop_reason = None
    for case in suite["cases"]:
        if stop_reason:
            break
        for repetition in range(1, 4):
            if stop_reason:
                break
            # Interleave methods to avoid putting one method in a different time window.
            methods = ("agent", "retrieval") if repetition % 2 else ("retrieval", "agent")
            for method in methods:
                if (case["id"], method, repetition) in completed:
                    continue
                state = ledger.summary(GRANT_ID)
                if state["status"] != "ready" or state["total_remaining_microyuan"] < 3_000_000:
                    stop_reason = "budget_or_receipt_gate"
                    break
                result = run_case(case, method, repetition, source=source, guard=guard,
                                  projects=projects, record=record, ledger=ledger, key=key,
                                  proxy_port=args.proxy_port, batch_id=batch_id)
                with results_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                    stream.flush()
                records.append(result)
                summary = summarize(records, suite)
                complete = len(records) == len(suite["cases"]) * 6
                write_report(args.output, summary, suite, complete=complete)
                print(json.dumps({"completed": len(records), "case": case["id"], "method": method,
                                  "status": result["outcome"]["status"], "concept_check": result["checks"]["concept_check_passed"],
                                  "fee_microyuan": result["spending"]["fee_microyuan"]}, ensure_ascii=False), flush=True)
                if result["spending"]["unresolved_requests"] or result["outcome"]["status"] in {"denied", "source_changed", "unverified"}:
                    stop_reason = "source_or_billing_failure"
                    break
    final = {"complete": len(records) == len(suite["cases"]) * 6, "stop_reason": stop_reason,
             "summary": summarize(records, suite), "cumulative_spending": ledger.summary(GRANT_ID)}
    (directory / "summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"complete": final["complete"], "runs": len(records), "directory": str(directory),
                      "report": str(args.output), "stop_reason": stop_reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
