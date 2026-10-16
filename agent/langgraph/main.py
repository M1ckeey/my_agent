"""Command-line entry point for checkpointed research."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import sys
from uuid import uuid4

from .checkpoint import open_checkpointer
from .lg_builder import create_graph

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"


def _fix_windows_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _safe_filename(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return text[:48] or "research"


def _save_report(topic: str, state, output_dir: Path = REPORT_DIR) -> Path:
    """Save a completed report and its recovery identifiers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = list(output_dir.glob(f"*_{_safe_filename(topic)}_{state.run_id[:8]}.md"))
    if existing:
        return existing[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{timestamp}_{_safe_filename(topic)}_{state.run_id[:8]}.md"
    lines = [
        state.report or "（没有生成报告）",
        "", "---", "",
        f"- 运行状态：{state.status}",
        f"- 研究步数：{state.depth}",
        f"- Findings：{len(state.findings)}",
        f"- Thread ID：{state.thread_id}",
        f"- Run ID：{state.run_id}",
        f"- 状态版本：{state.state_version}",
        "", "## 引用校验", "",
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


def _solve(topic: str, graph=None, *, thread_id: str | None = None) -> None:
    if graph is None:
        with open_checkpointer() as saver:
            _solve(topic, create_graph(checkpointer=saver), thread_id=thread_id)
        return
    thread_id = thread_id or uuid4().hex
    print(f"[线程] {thread_id}", flush=True)
    state = graph.run(topic, thread_id=thread_id)
    print(f"[运行] {state.run_id}")
    _, pending = graph.get_state(state.thread_id)
    if pending:
        print(f"[已中断] 下一节点：{', '.join(pending)}；使用 --resume --thread-id {state.thread_id} 恢复")
        return
    _print_result(state, _save_report(topic, state))


def main() -> None:
    _fix_windows_console()
    parser = argparse.ArgumentParser(description="运行、检查或恢复研究线程")
    parser.add_argument("topic", nargs="*")
    parser.add_argument("--thread-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--backend", choices=["memory", "postgres"])
    parser.add_argument("--pause-after", choices=["plan", "research", "critic", "revise", "write"])
    args = parser.parse_args()
    topic = " ".join(args.topic).strip()
    if (args.resume or args.status) and not args.thread_id:
        parser.error("--resume 和 --status 需要 --thread-id")
    with open_checkpointer(args.backend) as saver:
        graph = create_graph(
            checkpointer=saver,
            interrupt_after=[args.pause_after] if args.pause_after else None,
        )
        if args.status:
            state, pending = graph.get_state(args.thread_id)
            print(f"[线程] {state.thread_id} · [运行] {state.run_id}")
            print(f"[状态] {state.status} · 下一节点：{', '.join(pending) or '无'}")
            print(f"[进度] {len(state.completed_queries)} 个查询已完成，{len(state.frontier)} 个待处理")
            return
        if args.resume:
            state = graph.resume(args.thread_id)
            _, pending = graph.get_state(args.thread_id)
            if pending:
                print(f"[已中断] 下一节点：{', '.join(pending)}；使用 --resume --thread-id {args.thread_id} 恢复")
            else:
                _print_result(state, _save_report(state.topic, state))
            return
        if topic:
            _solve(topic, graph, thread_id=args.thread_id)
            return
        print("Research Agent（LangGraph 外层）—— 输入研究主题，空行或 exit 退出\n")
        thread_id = args.thread_id or uuid4().hex
        while True:
            try:
                topic = input("Research: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not topic or topic.lower() in {"exit", "quit"}:
                break
            try:
                _solve(topic, graph, thread_id=thread_id)
            except Exception as exc:
                print(f"\n[出错] {type(exc).__name__}: {exc}", file=sys.stderr)
            print()


if __name__ == "__main__":
    main()
