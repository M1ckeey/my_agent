"""ReAct 主循环：调模型 -> 执行 tool_calls -> 回填结果，直到模型直接作答或撞上 max_steps。

用的是 OpenAI 兼容的**原生 tool calling**：工具以 tools=[...] 传给模型，模型
返回结构化的 message.tool_calls，我们执行完把结果作为 role:"tool" 的消息回填。
模型不再输出任何约定格式的文本，所以这个文件里没有正则解析器了。

一个直接推论：**「finish」这个动作不存在了**。模型拿到足够信息后，会返回一条
不带 tool_calls 的普通消息，那条消息的内容就是最终答案——循环靠「有没有
tool_calls」判断该继续还是该收尾。

这个文件只管控制流，三样东西都不在这里了：
  发请求 / 拼流式分片  -> agent/llm.py
  按名字执行工具       -> tools.run_tool()
  子 agent             -> agent/subagent.py
"""

from __future__ import annotations

import os

from prompts import MAIN_SYSTEM
from tools import run_tool, to_openai_tools

from .llm import assistant_message, call_llm, final_text, get_client
from .hooks import install_default_hooks, trigger_hooks
from .state import AgentState, Step

# 提示词在 prompts.py（项目根目录）。放那儿是因为这个文件 import 了 tools，
# 而 tools/ 又要 import 提示词——根目录是唯一不参与这圈依赖的地方。


# ---------------------------------------------------------------- 流式打印

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


# ---------------------------------------------------------------- 主循环

def run_agent(
    task: str,
    *,
    max_steps: int = 8,
    verbose: bool = True,
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
            {"role": "system", "content": MAIN_SYSTEM},
            {"role": "user", "content": task},
        ],
    )
    install_default_hooks()
    trigger_hooks("UserPromptSubmit", task)

    client = get_client()
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

        trigger_hooks("BeforeModel", state)
        message = call_llm(client, model, state.messages, tools, on_text=printer)
        if printer:
            printer.close()

        # 没有 tool_calls = 模型认为信息够了，这条消息本身就是最终答案
        if not message.tool_calls:
            state.final_answer = final_text(message)
            continuation = trigger_hooks("Stop", state)
            if continuation is not None:
                state.final_answer = None
                state.messages.append({"role": "user", "content": str(continuation)})
                continue
            state.add_step(thought="", action="finish", action_input=state.final_answer)
            break

        # 带 tool_calls 的 assistant 消息必须原样回填进历史。少了它，下一轮
        # 模型会看到 role:"tool" 的消息却找不到对应的 tool_call，直接报错。
        state.messages.append(assistant_message(message))

        # 模型一轮可能返回多个 tool_call（并行调用），逐个执行、逐个回填，
        # 每个 tool_call_id 都必须有且只有一条对应的 tool 消息
        thought = (message.content or "").strip()
        for call in message.tool_calls:
            blocked = trigger_hooks("PreToolUse", state, call)
            if blocked is not None:
                observation = str(blocked)
            else:
                observation = run_tool(call.function.name, call.function.arguments)
            state.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": observation,
            })
            if blocked is None:
                trigger_hooks("PostToolUse", state, call, observation)
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
        trigger_hooks("Stop", state)
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
