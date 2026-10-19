"""Compute conservative, reproducible metrics from saved raw artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

EVAL_DIR = Path(__file__).resolve().parent
METRIC_VERSION = "v3-seven-2026-10-19"


def _metric(status: str, value=None, reason: str = "", **details) -> dict:
    return {"status": status, "value": value, "reason": reason, **details}


def _specific_source(source: str) -> bool:
    return source.startswith(("http://", "https://", "arxiv:", "file:"))


def _seven_for_question(row: dict, raw: dict, citations: list[dict], output_dir: Path) -> dict:
    report = raw.get("report") or ""
    headings = len(re.findall(r"(?m)^#{1,2}\s+\S", report))
    conditions = {
        "done": raw.get("status") == "done",
        "no_error": raw.get("error") is None and bool(raw),
        "at_least_300_chars": len(report) >= 300,
        "at_least_2_headings": headings >= 2,
    }
    completion = _metric("measured", all(conditions.values()), conditions=conditions)

    review_path = output_dir / "review" / f"{row['id']}.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    source_specific = bool(citations) and all(
        _specific_source(str(item.get("source", ""))) for item in citations
    )
    if not source_specific:
        citation = _metric("unavailable", reason="报告没有可逐条核对的具体来源引用")
    elif not review.get("reviewer") or not isinstance(review.get("citation_checks"), list):
        citation = _metric("pending_review", reason="具体来源已有，尚缺逐条存在性与忠实性复核")
    else:
        checks = review["citation_checks"]
        if len(checks) != len(citations) or any(
            type(item.get("exists")) is not bool or type(item.get("faithful")) is not bool
            for item in checks
        ):
            raise ValueError(f"{row['id']} 的 citation_checks 与引用数不一致或不是布尔判定")
        existing = sum(item["exists"] for item in checks)
        faithful = sum(item["exists"] and item["faithful"] for item in checks)
        citation = _metric(
            "measured" if existing else "unavailable",
            faithful / existing if existing else None,
            reason="无存在的引用来源" if not existing else "",
            total=len(checks), existing=existing, faithful=faithful,
            existence_rate=existing / len(checks), reviewer=review["reviewer"],
        )

    expected = row.get("expected_subquestions") or []
    if row.get("annotation_status") != "calibrated":
        coverage = _metric("pending_review", reason="预期子问题尚未人工校准")
    elif not review.get("reviewer") or not isinstance(review.get("coverage_checks"), list):
        coverage = _metric("pending_review", reason="缺少预期子问题逐项复核")
    else:
        checks = review["coverage_checks"]
        if len(checks) != len(expected) or any(type(value) is not bool for value in checks):
            raise ValueError(f"{row['id']} 的 coverage_checks 与预期子问题数不一致")
        coverage = _metric(
            "measured", sum(checks) / len(checks) if checks else None,
            covered=sum(checks), total=len(checks), reviewer=review["reviewer"],
        )

    token = raw.get("token_used")
    token_cost = _metric(
        "partial" if token is not None else "unavailable",
        reason="仅有 ResearchState 内部 token 计数，缺输入/输出用量及价格" if token is not None else "模型用量缺失",
        state_tokens=token, amount=None,
    )
    decisions = raw.get("reflection_log")
    steps = _metric(
        "measured" if isinstance(decisions, list) else "unavailable",
        len(decisions) if isinstance(decisions, list) else None,
        reason="未持久化 Critic 决策轮次" if not isinstance(decisions, list) else "",
        research_depth=raw.get("depth"),
    )
    hard_reasons = []
    if raw.get("depth") is not None and raw.get("max_depth") is not None and raw["depth"] >= raw["max_depth"]:
        hard_reasons.append("max_depth")
    if raw.get("token_used") is not None and raw.get("max_tokens") is not None and raw["token_used"] >= raw["max_tokens"]:
        hard_reasons.append("max_tokens")
    if raw.get("status") != "done":
        stop_type = None
    elif hard_reasons:
        stop_type = "hard_stop"
    elif raw.get("critic_signal") == "stop":
        stop_type = "critic_stop"
    else:
        stop_type = "other"
    reflection = _metric(
        "partial" if stop_type else "unavailable",
        stop_type,
        reason="仅判定终态停止类型；缺逐轮决策及覆盖复核" if stop_type else "无法判定终态停止类型",
        hard_reasons=hard_reasons,
    )
    return {
        "completion_rate": completion,
        "citation_accuracy": citation,
        "information_coverage": coverage,
        "retrieval_hit_rate": _metric("not_applicable", reason="当前题集没有 RAG 检索目标标注"),
        "token_cost": token_cost,
        "average_steps": steps,
        "reflection_effectiveness": reflection,
    }


def score(output_dir: Path) -> dict:
    meta = json.loads((EVAL_DIR / "dataset-meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (EVAL_DIR / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["dataset_version"] != meta["version"]:
        raise ValueError("题集版本与运行产物不一致")
    per_question = []
    for row in rows:
        path = output_dir / "raw" / f"{row['id']}.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        checks = raw.get("citations") or []
        citations = [item for item in checks if item.get("type") == "citation"]
        coverage_checks = [item for item in checks if item.get("type") == "coverage"]
        source_matched = sum(bool(c.get("exists")) for c in citations)
        # This checks that a report citation was retained as a finding. It does
        # not establish that the external URL exists or that the claim is faithful.
        specific_sources = sum(_specific_source(str(c.get("source", ""))) for c in citations)
        report = raw.get("report") or ""
        per_question.append({
            "id": row["id"],
            "category": row["category"],
            "status": raw.get("status", "missing"),
            "completed": raw.get("status") == "done" and bool(report.strip()),
            "citation_count": len(citations),
            "citation_finding_matches": source_matched,
            "citation_finding_match_rate": None if not citations else source_matched / len(citations),
            "specific_source_citations": specific_sources,
            "citation_traceability": None,
            "planner_subquestions_with_findings": sum(bool(c.get("exists")) for c in coverage_checks),
            "planner_subquestion_count": len(coverage_checks),
            "depth": raw.get("depth"),
            "token_used_reported": raw.get("token_used"),
            "elapsed_seconds": raw.get("elapsed_seconds"),
            "error": raw.get("error"),
            "subquestion_coverage": None,
            "claim_support": None,
            "seven_metrics": _seven_for_question(row, raw, citations, output_dir),
        })
    totals = Counter(item["status"] for item in per_question)
    cited = sum(item["citation_count"] for item in per_question)
    matched = sum(item["citation_finding_matches"] for item in per_question)
    specific = sum(item["specific_source_citations"] for item in per_question)
    planner_count = sum(item["planner_subquestion_count"] for item in per_question)
    planner_covered = sum(item["planner_subquestions_with_findings"] for item in per_question)
    seven = [item["seven_metrics"] for item in per_question]
    complete = sum(item["completion_rate"]["value"] for item in seven)
    cite_measured = [item["citation_accuracy"] for item in seven if item["citation_accuracy"]["status"] == "measured"]
    cover_measured = [item["information_coverage"] for item in seven if item["information_coverage"]["status"] == "measured"]
    step_measured = [item["average_steps"]["value"] for item in seven if item["average_steps"]["status"] == "measured"]
    critic_stops = sum(item["reflection_effectiveness"]["value"] == "critic_stop" for item in seven)
    hard_stops = sum(item["reflection_effectiveness"]["value"] == "hard_stop" for item in seven)
    seven_summary = {
        "completion_rate": _metric("measured", complete / len(rows), passed=complete, total=len(rows)),
        "citation_accuracy": _metric(
            "measured" if len(cite_measured) == len(rows) else "unavailable",
            sum(item["faithful"] for item in cite_measured) / sum(item["existing"] for item in cite_measured)
            if len(cite_measured) == len(rows) and sum(item["existing"] for item in cite_measured) else None,
            reason="仍有题目缺具体来源或逐条复核" if len(cite_measured) != len(rows) else "",
            measured_questions=len(cite_measured),
        ),
        "information_coverage": _metric(
            "measured" if len(cover_measured) == len(rows) else "pending_review",
            sum(item["covered"] for item in cover_measured) / sum(item["total"] for item in cover_measured)
            if len(cover_measured) == len(rows) and sum(item["total"] for item in cover_measured) else None,
            reason="仍有题目缺校准标注或逐项复核" if len(cover_measured) != len(rows) else "",
            measured_questions=len(cover_measured),
        ),
        "retrieval_hit_rate": _metric("not_applicable", reason="当前题集没有 RAG 检索目标标注"),
        "token_cost": _metric("partial", reason="仅有状态 token 计数，无法按模型计费", state_tokens=sum(item["token_used_reported"] or 0 for item in per_question), amount=None),
        "average_steps": _metric("measured" if len(step_measured) == len(rows) else "unavailable", sum(step_measured) / len(rows) if len(step_measured) == len(rows) else None, reason="仍有题目缺 Critic 轮次日志" if len(step_measured) != len(rows) else ""),
        "reflection_effectiveness": _metric("partial", critic_stops / len(rows), reason="只有终态结构性停止分类，缺逐轮决策与人工质量复核", critic_stops=critic_stops, hard_stops=hard_stops, total=len(rows)),
    }
    summary = {
        "metric_version": METRIC_VERSION,
        "seven_metrics": seven_summary,
        "evaluation_complete": all(item["status"] in {"measured", "not_applicable"} for item in seven_summary.values()),
        "dataset_version": meta["version"],
        "dataset_status": meta["status"],
        "question_count": len(rows),
        "completed_count": sum(item["completed"] for item in per_question),
        "completion_rate": sum(item["completed"] for item in per_question) / len(rows),
        "statuses": dict(totals),
        "citation_count": cited,
        "citation_finding_matches": matched,
        "citation_finding_match_rate": None if not cited else matched / cited,
        "specific_source_citations": specific,
        "citation_traceability": None,
        "planner_subquestions_with_findings": planner_covered,
        "planner_subquestion_count": planner_count,
        "planner_subquestion_material_rate": None if not planner_count else planner_covered / planner_count,
        "subquestion_coverage": None,
        "claim_support": None,
        "per_question": per_question,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = score(args.output_dir)
    print(json.dumps({key: value for key, value in summary.items() if key != "per_question"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
