"""请求主循环在当前工具批次完成后压缩会话历史。"""

from __future__ import annotations


def compact() -> str:
    """整理较早的对话历史，保留当前任务和后续工作所需的信息。"""
    return "已请求在本轮工具执行完成后压缩上下文。"


__all__ = ["compact"]
