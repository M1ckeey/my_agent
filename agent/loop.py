"""ReAct 主循环：调模型 -> 执行 tool_calls -> 回填结果，直到模型直接作答或撞上 max_steps。

用的是 OpenAI 兼容的**原生 tool calling**：工具以 tools=[...] 传给模型，模型
返回结构化的 message.tool_calls，我们执行完把结果作为 role:"tool" 的消息回填。
模型不再输出任何约定格式的文本，所以这个文件里没有正则解析器了。

一个直接推论：**「finish」这个动作不存在了**。模型拿到足够信息后，会返回一条
不带 tool_calls 的普通消息，那条消息的内容就是最终答案——循环靠「有没有
tool_calls」判断该继续还是该收尾。

工具元数据（名字、说明、参数 schema）全部由 tools/base.py 从函数本身推导，
这个文件只管控制流。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from tools import TOOLS, to_openai_tools

from .state import AgentState, Step

# 显式锚定项目根目录的 .env，这样从任何 cwd 启动都能读到。
# 默认不覆盖已存在的真实环境变量——shell 里 export 的优先级更高。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------- 提示词

# 工具清单不在这里了——它随 tools=[...] 一起走结构化通道，比写进提示词更准。
# 提示词只留下工具描述里说不清的东西：什么时候该用工具、什么时候该收尾。
_SYSTEM_PROMPT = """你是一个通过调用工具来解决问题的智能体。

规则：
- 需要计算、联网查资料或搜论文时，调用对应的工具，不要凭记忆猜
- 工具返回的结果会作为新消息给你，据此决定下一步
- 一轮里可以调用多个工具，也可以分多轮调用
- 信息够了就直接给出最终答案，不要再调用工具——你的回答本身就是终点
- 工具返回错误时，看懂错误再换个做法重试，不要原样重发同一个调用
"""


# ---------------------------------------------------------------- LLM

def _make_client() -> OpenAI:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        raise SystemExit(
            f"缺少 API key。\n\n"
            f"打开 {env_path}\n"
            f"把这一行填上你的 key，保存后重跑即可（不用重启终端）：\n\n"
            f"    LLM_API_KEY=sk-xxxxxxxx\n\n"
            f"或者用环境变量临时覆盖（优先级高于 .env）：\n"
            f'  export LLM_API_KEY="sk-..."        # Git Bash / macOS / Linux\n'
            f'  $env:LLM_API_KEY = "sk-..."        # PowerShell'
        )
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    )


def _call_llm(
    client: OpenAI, model: str, messages: list[dict], tools: list[dict]
) -> Any:
    """调一次模型，返回完整的 message 对象。

    返回对象而不是纯文本，是因为 tool_calls 挂在 message 上而不是 content 里——
    文本模式下那行 `text = message.content` 在这一版里没有意义了。
    """
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
    }

    # 工具为空时不要发 tools=[]，有的服务端会因此报错
    if tools:
        kwargs["tools"] = tools

    # 推理型模型不接受 temperature，设 LLM_TEMPERATURE="" 即可不发送
    temp = os.getenv("LLM_TEMPERATURE", "0")
    if temp != "":
        kwargs["temperature"] = float(temp)

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message


def _final_text(message: Any) -> str:
    """从一条没有 tool_calls 的消息里取出最终答案。"""
    content = (message.content or "").strip()
    if content:
        return content
    # 有的推理模型把内容放在 reasoning_content 里，content 是空的
    return (getattr(message, "reasoning_content", None) or "").strip()


def _run_tool(call: Any) -> str:
    """执行一个 tool_call，返回要回填给模型的文本。

    任何异常都变成回填内容而不是让循环崩掉——模型看到错误信息能自己纠错，
    这是 ReAct 里「把错误当观察」的全部意义。
    """
    name = call.function.name
    tool = TOOLS.get(name)
    if tool is None:
        return f"错误：没有名为 {name!r} 的工具。可用工具：{list(TOOLS)}"

    try:
        # 模型给的是一个 JSON 字符串；空串按「无参数」处理
        kwargs = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        return (
            f"错误：工具参数不是合法 JSON：{exc}。"
            f"你发的是 {call.function.arguments!r}"
        )

    if not isinstance(kwargs, dict):
        return f"错误：工具参数必须是一个 JSON 对象，你发的是 {type(kwargs).__name__}"

    try:
        return tool.run(**kwargs)
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- 主循环

def run_agent(
    task: str,
    *,
    max_steps: int = 8,
    verbose: bool = True,
    client: OpenAI | None = None,
    model: str | None = None,
) -> AgentState:
    """跑一轮 ReAct，返回结束时的状态。

    Args:
        task: 用户的问题。
        max_steps: 最多走几步（一次模型调用算一步），防止模型绕圈子烧钱。
        verbose: 是否把每一步打到终端。
    """
    state = AgentState(
        task=task,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ],
    )

    client = client or _make_client()
    model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")
    tools = to_openai_tools()

    for _ in range(max_steps):
        message = _call_llm(client, model, state.messages, tools)

        # 没有 tool_calls = 模型认为信息够了，这条消息本身就是最终答案
        if not message.tool_calls:
            state.final_answer = _final_text(message)
            step = state.add_step(
                thought="",
                action="finish",
                action_input=state.final_answer,
            )
            if verbose:
                _print_step(step)
            break

        # 带 tool_calls 的 assistant 消息必须原样回填进历史。少了它，下一轮
        # 模型会看到 role:"tool" 的消息却找不到对应的 tool_call，直接报错。
        state.messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ],
        })

        # 模型一轮可能返回多个 tool_call（并行调用），逐个执行、逐个回填，
        # 每个 tool_call_id 都必须有且只有一条对应的 tool 消息
        thought = (message.content or "").strip()
        for call in message.tool_calls:
            observation = _run_tool(call)
            state.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": observation,
            })
            step = state.add_step(
                thought=thought,
                action=call.function.name,
                action_input=call.function.arguments or "",
                observation=observation,
            )
            if verbose:
                _print_step(step)
    else:
        # 循环跑满还没 break
        state.final_answer = f"（达到最大步数 {max_steps}，任务未完成）"

    return state


def _print_step(step: Step) -> None:
    print(f"\n── step {step.index} ──")
    if step.thought:
        print(f"  Thought     : {step.thought}")
    if step.action == "finish":
        print(f"  Finish      : {step.action_input}")
        return
    print(f"  Action      : {step.action}")
    if step.action_input:
        print(f"  Action Input: {step.action_input}")
    if step.observation is not None:
        print(f"  Observation : {step.observation}")
