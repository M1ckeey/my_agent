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
    # 答案由 run_agent 边生成边流式打出来了，这里不再重打一遍——
    # 否则同一段文字在终端出现两次。verbose=False 是给「要 state 不要输出」
    # 的调用方的，那时自己去读 state.final_answer。
    run_agent(task, verbose=True)


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
