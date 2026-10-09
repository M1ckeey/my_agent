"""LLM 调用层：建客户端、调一次模型、把流式分片拼成一条完整消息。

从 loop.py 抽出来是因为多了一个消费方——子 agent 也要调模型。放在这里两边都能用。
这个文件只管「怎么和模型说话」，不管「什么时候说」：控制流在 loop.py（主循环）
和 subagent.py（子 agent）里。

客户端做成进程内单例。OpenAI SDK 底层是 httpx 连接池，本身就线程安全，所以并行
跑子 agent 时共用一份连接池，比为每个子 agent 新建一个省一次 TLS 握手。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, Timeout

# 显式锚定项目根目录的 .env，这样从任何 cwd 启动都能读到。
# 默认不覆盖已存在的真实环境变量——shell 里 export 的优先级更高。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ---------------------------------------------------------------- 拼装结果

# 流式返回的是一堆增量分片，要自己拼回一条完整消息。下面三个类型就是拼装结果，
# **字段名刻意和 SDK 的对象保持一致**（content / tool_calls / id / function.name /
# function.arguments），这样调用方一行都不用改。


@dataclass
class FunctionCall:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: FunctionCall


@dataclass
class Message:
    content: str | None
    tool_calls: list[ToolCall] | None
    reasoning_content: str | None = None
    total_tokens: int = 0


# ---------------------------------------------------------------- 客户端

_client: OpenAI | None = None
_client_lock = threading.Lock()


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
        #   read    —— 流式下它算的是「两个 token 之间」的间隔（见 call_llm 的
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


def get_client() -> OpenAI:
    """进程内单例。并行跑子 agent 时共用一份 httpx 连接池。

    双重检查加锁：已经建好就直接返回（读路径不上锁），只有首次构造才竞争锁。
    为什么这里值得上锁——子 agent 是并发起的，几个线程会同时走到这一行。
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = _make_client()
    return _client


# ---------------------------------------------------------------- 重试 + 调模型

# 值得重试的只有**传输层**这两种：连接没建起来 / 建起来又断了 / 超时。
# 其余一律不重试——4xx 是 key 不对或参数写错，重发只是白等；这个判断和
# web_search 的 _RETRY_STATUSES 是同一条理由。
# 注意 APIStatusError（4xx/5xx）根本不在这个元组里，所以它天然不会被重试。
_RETRYABLE = (APIConnectionError, APITimeoutError)

# SDK 自己会重试（LLM_MAX_RETRIES，默认 2），但那个窗口小得没什么用：
# INITIAL_RETRY_DELAY=0.5 秒起步、按 2^n 退避，max_retries=2 三次尝试加起来
# **只覆盖约 1.5 秒**的不可达（见 openai/_constants.py）。
# 实测踩到的那次抖动比这长，三次全失败，于是整轮研究直接结束、什么都没留下。
# 所以在外面再套一层，把容忍窗口从「1.5 秒」拉到「十几秒」。
#
# 为什么不干脆把 LLM_MAX_RETRIES 调大：那个值会叠加进这层，两层相乘。分开设才有
# 意义——内层密集、外层稀疏，短抖动由内层秒杀，长抖动由外层兜住。
_DEFAULT_RETRIES = 3
_DEFAULT_RETRY_DELAY = 1.0


def _retry_settings() -> tuple[int, float]:
    """读重试配置。写坏了就退回默认值，不因为一个笔误让整个程序起不来。"""
    try:
        count = int(os.getenv("LLM_RETRIES", str(_DEFAULT_RETRIES)))
    except ValueError:
        count = _DEFAULT_RETRIES
    try:
        delay = float(os.getenv("LLM_RETRY_DELAY", str(_DEFAULT_RETRY_DELAY)))
    except ValueError:
        delay = _DEFAULT_RETRY_DELAY
    return max(0, count), max(0.0, delay)


def call_llm(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
) -> Message:
    """调一次模型，流式接收并拼成一条完整消息。传输层失败会自动重试。

    返回对象而不是纯文本，是因为 tool_calls 挂在 message 上而不是 content 里。

    流式是为了让调用方边生成边显示：非流式的话，模型逐字生成的整个过程里
    终端一个字都不显示，看起来就像卡死了。on_text 每收到一片 content 回调一次。

    重试只针对连接类/超时类错误，退避 1s、2s、4s。**一旦已经有文字吐给 on_text
    就不再重试**——重试会把同一段话再打一遍。传输层失败几乎都发生在拿到第一个字
    之前，所以这条限制很少真的挡住重试。

    Args:
        on_text: 收到 content 分片时回调，传 None 就只静默拼装。子 agent 走这条——
            多个子 agent 同时往一个终端写会糊成一团。
        max_tokens: 覆盖 LLM_MAX_TOKENS。主循环不用传，planner 要（它的输出是
            一份 JSON 计划，比 ReAct 的一步长得多）。
    """
    retries, base_delay = _retry_settings()

    for attempt in range(retries + 1):
        shown = False

        def _spy(text: str) -> None:
            nonlocal shown
            shown = True
            if on_text:
                on_text(text)

        try:
            return _call_once(
                client, model, messages, tools, _spy if on_text else None, max_tokens
            )
        except _RETRYABLE as exc:
            if attempt >= retries or shown:
                # 放弃前把底层原因补进消息里。SDK 的 APIConnectionError 文案只有一句
                # 干巴巴的 "Connection error."，真正有用的信息全在 __cause__ 里：
                # ConnectError = 路不通，RemoteProtocolError = 连上了但被对端关掉。
                # 这两个指向完全不同的修法，不带上就永远只能猜——实测那次就是这么
                # 卡住的，报错里一个字都没提底层是什么。
                parts = []
                cause = exc.__cause__
                if cause is not None:
                    parts.append(f"底层：{type(cause).__name__}: {str(cause)[:120]}")
                if attempt:
                    parts.append(f"已重试 {attempt} 次")
                # 幂等：同一个异常对象万一被上层缓存后重抛，别把底层信息追加两遍
                if parts and exc.args and not getattr(exc, "_cause_added", False):
                    exc.args = (f"{exc.args[0]}（{'；'.join(parts)}）",)
                    exc._cause_added = True
                raise
            delay = base_delay * (2**attempt)
            # 打到 stderr：不能走 stdout，那会把流式的正文搅乱。
            print(
                f"[llm] {type(exc).__name__}，{delay:.0f}s 后重试（{attempt + 1}/{retries}）",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

    raise AssertionError("重试循环必须以 return 或 raise 结束")  # pragma: no cover


def _call_once(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
) -> Message:
    """真正发一次请求。重试的事由 call_llm 管，这里只管这一次。"""
    kwargs: dict = {
        "model": model,
        "messages": messages,
        # 兜底值必须和 .env 里的 LLM_MAX_TOKENS 一致，不能贪小。原来的 1024 是
        # 「ReAct 每步只输出三行」时代的估值，加了 delegate 之后一轮回复里可能塞着
        # 好几条任务描述，1024 会把 tool_call 参数拦腰截断成非法 JSON（踩过）。
        # 这个默认值只在 .env 那一行被删掉时才生效——那正是它危险的地方：修好的
        # bug 会沿着一个没人看的默认值悄悄回来，所以两边必须保持一致。
        "max_tokens": max_tokens or int(os.getenv("LLM_MAX_TOKENS", "4096")),
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
    total_tokens = 0

    for chunk in client.chat.completions.create(**kwargs):
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            value = getattr(usage, "total_tokens", None)
            if value is None and isinstance(usage, dict):
                value = usage.get("total_tokens")
            if value is not None:
                total_tokens = int(value)
        if not chunk.choices:
            continue  # 末尾那片只带 usage，没有 choices
        delta = chunk.choices[0].delta

        if delta.content:
            text_parts.append(delta.content)
            if on_text:
                on_text(delta.content)

        # 推理型模型把思考放在这个字段里，最后取答案时要用（见 final_text）
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

    return Message(
        content="".join(text_parts) or None,
        tool_calls=[
            ToolCall(id=slot["id"], function=FunctionCall(slot["name"], slot["arguments"]))
            for _, slot in sorted(slots.items())
        ] or None,
        reasoning_content="".join(reason_parts) or None,
        total_tokens=total_tokens,
    )


def final_text(message: Any) -> str:
    """从一条没有 tool_calls 的消息里取出最终答案。"""
    content = (message.content or "").strip()
    if content:
        return content
    # 有的推理模型把内容放在 reasoning_content 里，content 是空的
    return (getattr(message, "reasoning_content", None) or "").strip()


def assistant_message(message: Message) -> dict:
    """把拼装出来的 Message 转回 OpenAI chat 格式的 assistant 消息。

    带 tool_calls 的 assistant 消息必须**原样**回填进历史。少了它，下一轮模型会
    看到 role:"tool" 的消息却找不到对应的 tool_call，直接报错。
    """
    return {
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
            for call in message.tool_calls or []
        ],
    }
