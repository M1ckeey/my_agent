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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI, Timeout

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


# ---------------------------------------------------------------- 流式拼装

# 流式返回的是一堆增量分片，要自己拼回一条完整消息。下面三个类型就是拼装结果，
# **字段名刻意和 SDK 的对象保持一致**（content / tool_calls / id / function.name /
# function.arguments），这样 _run_tool 和 run_agent 一行都不用改。


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function


@dataclass
class _Message:
    content: str | None
    tool_calls: list[_ToolCall] | None
    reasoning_content: str | None = None


class _LivePrinter:
    """边生成边打：首片之前补缩进，最后补一个换行。

    不做任何标注——此刻还不知道这段文字是「Thought」还是「最终答案」，
    那是拿到完整消息之后才知道的事。硬贴一个标签反而会贴错。
    """

    def __init__(self) -> None:
        self._started = False

    def __call__(self, text: str) -> None:
        if not text:
            return
        if not self._started:
            print("  ", end="", flush=True)
            self._started = True
        print(text, end="", flush=True)

    def close(self) -> None:
        if self._started:
            print()


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
        # 超时得分开设，一个标量喂不饱这两种情况：
        #   connect —— 端点不通就该快点失败，10 秒足够
        #   read    —— 流式下它算的是「两个 token 之间」的间隔（见 _call_llm 的
        #              注释），不是生成总时长，所以给宽：600 秒 = 连续 10 分钟
        #              一个字都没吐才算超时
        # 直接写 timeout=600 会把 connect 也变成 600，服务器不可达就干等 10 分钟。
        timeout=Timeout(
            connect=float(os.getenv("LLM_CONNECT_TIMEOUT", "10")),
            read=float(os.getenv("LLM_READ_TIMEOUT", "600")),
            write=30.0,
            pool=10.0,
        ),
        # 值和 SDK 默认一致，显式写出来是为了能调、也让读者知道这里有重试。
        # SDK 只重试连接层错误和 429/5xx，不会重试「模型答了但格式不对」。
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


def _call_llm(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], None] | None = None,
) -> _Message:
    """调一次模型，流式接收并拼成一条完整消息。

    返回对象而不是纯文本，是因为 tool_calls 挂在 message 上而不是 content 里——
    文本模式下那行 `text = message.content` 在这一版里没有意义了。

    流式是为了让调用方边生成边显示：非流式的话，模型逐字生成的整个过程里
    终端一个字都不显示，看起来就像卡死了。on_text 每收到一片 content 回调一次。

    Args:
        on_text: 收到 content 分片时回调，传 None 就只静默拼装（verbose=False 走这条）。
    """
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "1024")),
        "stream": True,
    }

    # 工具为空时不要发 tools=[]，有的服务端会因此报错
    if tools:
        kwargs["tools"] = tools

    # 推理型模型不接受 temperature，设 LLM_TEMPERATURE="" 即可不发送
    temp = os.getenv("LLM_TEMPERATURE", "0")
    if temp != "":
        kwargs["temperature"] = float(temp)

    text_parts: list[str] = []
    reason_parts: list[str] = []
    # tool_call 是分片下发的：同一个 index 的多片要拼起来，所以先按 index 归并
    slots: dict[int, dict] = {}

    for chunk in client.chat.completions.create(**kwargs):
        if not chunk.choices:
            continue  # 末尾那片只带 usage，没有 choices
        delta = chunk.choices[0].delta

        if delta.content:
            text_parts.append(delta.content)
            if on_text:
                on_text(delta.content)

        # 推理型模型把思考放在这个字段里，最后取答案时要用（见 _final_text）
        if getattr(delta, "reasoning_content", None):
            reason_parts.append(delta.reasoning_content)

        for call in getattr(delta, "tool_calls", None) or []:
            # index = 这是本轮第几个 tool_call，不是分片序号
            slot = slots.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
            if call.id:
                slot["id"] = call.id
            if call.function is not None:
                # name 正常只在首片出现且是完整的，用 += 也能兼容被拆开的情况
                slot["name"] += call.function.name or ""
                slot["arguments"] += call.function.arguments or ""

    return _Message(
        content="".join(text_parts) or None,
        tool_calls=[
            _ToolCall(id=slot["id"], function=_Function(slot["name"], slot["arguments"]))
            for _, slot in sorted(slots.items())
        ] or None,
        reasoning_content="".join(reason_parts) or None,
    )


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

    # turn 数的是**模型调用次数**，和 max_steps 对齐；而 step.index 数的是
    # 工具执行次数（一轮返回 3 个并行 tool_call 就是 3 个 step），两者会分叉。
    for turn in range(1, max_steps + 1):
        # 表头要先打，再流式——不然模型吐出来的文字会冒在 "── step N ──" 上面，
        # 看着像是属于上一步的。
        printer = _LivePrinter() if verbose else None
        if verbose:
            print(f"\n── step {turn} ──")

        message = _call_llm(client, model, state.messages, tools, on_text=printer)
        if printer:
            printer.close()

        # 没有 tool_calls = 模型认为信息够了，这条消息本身就是最终答案
        if not message.tool_calls:
            state.final_answer = _final_text(message)
            state.add_step(thought="", action="finish", action_input=state.final_answer)
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
    """只打 step 的结构化字段。

    表头和模型自己说的话都不在这里：表头由 run_agent 在调模型**之前**打
    （要赶在流式输出前面），模型的话也已经边生成边打过了——在这里再打一遍
    就是把同一段文字输出两次。finish 那一步同理，答案已经流式显示过。
    """
    print(f"  Action      : {step.action}")
    if step.action_input:
        print(f"  Action Input: {step.action_input}")
    if step.observation is not None:
        print(f"  Observation : {step.observation}")
