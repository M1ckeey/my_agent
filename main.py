"""入口。

    python main.py "123 * 456 等于多少"     # 一次性
    python main.py                          # 交互模式

环境变量：
    LLM_API_KEY       必填
    LLM_BASE_URL      默认 https://api.deepseek.com/v1
    LLM_MODEL         默认 deepseek-v4-flash
    LLM_TEMPERATURE   默认 0；设为空字符串则不发送（推理型模型用）
"""

from __future__ import annotations

import sys

from agent.loop import run_agent


def _fix_windows_console() -> None:
    """Windows 终端默认 GBK，打中文会炸。"""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _solve(task: str) -> None:
    state = run_agent(task)
    print(f"\n{'=' * 60}\n最终答案：{state.final_answer}\n{'=' * 60}")


def main() -> None:
    _fix_windows_console()

    task = " ".join(sys.argv[1:]).strip()
    if task:
        _solve(task)
        return

    print("ReAct Agent（阶段一）—— 输入问题，空行或 exit 退出\n")
    while True:
        try:
            task = input("Question: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task or task.lower() in {"exit", "quit"}:
            break
        try:
            _solve(task)
        except Exception as exc:
            print(f"\n[出错] {type(exc).__name__}: {exc}", file=sys.stderr)
        print()


if __name__ == "__main__":
    main()
