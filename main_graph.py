"""LangGraph 外层研究流程入口。

    python main_graph.py "调研 Python 3.13 的主要新特性"  # 一次性
    python main_graph.py                                  # 交互模式

外层流程：
    plan → research → critic → revise/research → write → validate
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys

from agent.graph import create_graph

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def _fix_windows_console() -> None:
    """Windows 终端默认 GBK，打中文会炸。"""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _safe_filename(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return (text[:48] or "research")


def _save_report(topic: str, state, output_dir: Path = REPORT_DIR) -> Path:
    """Save one completed research report and its run metadata."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{timestamp}_{_safe_filename(topic)}.md"

    lines = [
        state.report or "（没有生成报告）",
        "",
        "---",
        "",
        f"- 运行状态：{state.status}",
        f"- 研究步数：{state.depth}",
        f"- Findings：{len(state.findings)}",
        "",
        "## 引用校验",
        "",
    ]
    if state.citations:
        for citation in state.citations:
            mark = "通过" if citation.get("exists") else "失败"
            note = f"：{citation['note']}" if citation.get("note") else ""
            lines.append(f"- [{mark}] {citation.get('source', '')}{note}")
    else:
        lines.append("- 未检测到报告来源")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _print_result(state, report_path: Path | None = None) -> None:
    """打印报告和引用校验结果。"""
    print("\n" + (state.report or "（没有生成报告）"))
    print(f"\n[流程状态] {state.status} · 研究步数 {state.depth}")
    if report_path is not None:
        print(f"[报告已保存] {report_path}")

    if not state.citations:
        print("[引用校验] 没有检测到报告来源")
        return

    passed = sum(1 for citation in state.citations if citation.get("exists"))
    print(f"[引用校验] {passed}/{len(state.citations)} 条来源存在")
    for citation in state.citations:
        mark = "通过" if citation.get("exists") else "失败"
        note = f"：{citation['note']}" if citation.get("note") else ""
        print(f"  - [{mark}] {citation.get('source', '')}{note}")


def _solve(topic: str) -> None:
    graph = create_graph()
    state = graph.run(topic)
    report_path = _save_report(topic, state)
    _print_result(state, report_path)


def main() -> None:
    _fix_windows_console()

    topic = " ".join(sys.argv[1:]).strip()
    if topic:
        _solve(topic)
        return

    print("Research Agent（LangGraph 外层）—— 输入研究主题，空行或 exit 退出\n")
    while True:
        try:
            topic = input("Research: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not topic or topic.lower() in {"exit", "quit"}:
            break
        try:
            _solve(topic)
        except Exception as exc:
            print(f"\n[出错] {type(exc).__name__}: {exc}", file=sys.stderr)
        print()


if __name__ == "__main__":
    main()
