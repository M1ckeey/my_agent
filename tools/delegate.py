"""delegate：把多个互相独立的子任务一次性派给并行的子 agent。

为什么是「一批」而不是「一个」：单任务版每次调用只能起一个子 agent，想并行就得靠
主循环一轮里发多个 tool_call，而主循环是**串行执行** tool_call 的（loop.py 里那个
for 循环）——那样其实一点也没并行起来。批量版把并发放在工具内部，才真的同时跑。

成本是这个工具唯一的风险。一次 delegate 的子 agent 同时烧 token，所以这里对任务数
和步数都设了硬上限；上限不是「建议」，超了直接拒绝，让模型自己收敛到合理的拆法。

这个模块**不在模块级 import agent.***，原因见 plan.py 的模块 docstring。
"""

from __future__ import annotations

import os

# 一次最多派几个子任务。这是成本闸门：每个子任务是一个独立的子 agent，
# 各自会跑最多 SUBAGENT_MAX_STEPS 次模型调用。
_MAX_TASKS = 8

# 每个子 agent 的默认步数。真值由 SUBAGENT_MAX_STEPS 决定，这里只是它的兜底，
# 也是文档里的那个数。
_DEFAULT_STEPS = 5

# 步数的安全上限，封的是**模型自己填的** max_steps——它张口要 1000 步会把整批烧穿。
# 但它必须**高于**默认值，否则 .env 的 SUBAGENT_MAX_STEPS 就变成一个改了没反应的
# 死旋钮（原来写死 6，.env 里配 10 也只跑 6）。这和「默认值写在函数签名里」是同一类
# bug：多了一个隐藏的第二真相来源。
# 10 对应「一个子任务最多查四五个源」的量级，再往上该做的是多拆几个子任务。
_MAX_STEPS_CEILING = 10


def _validate(raw: object, limit: int) -> list[str]:
    """把模型给的 tasks 参数收敛成一个干净的字符串列表。

    报错的措辞按「模型能看懂并自己改」来写——这是 ReAct 里错误变 Observation 的
    全部意义，所以每种错误都说清楚它到底发来了什么、应该发什么。
    """
    if isinstance(raw, str):
        # 有的模型会把数组序列化成字符串发过来。不静默兜底：JSON 字符串和数组
        # 是两种东西，猜错了会把整段文本当成一个任务派出去，结果更难看懂。
        raise ValueError(
            f"tasks 要的是一个数组，你发的是字符串 {raw[:80]!r}。"
            f'请改成 ["子任务一", "子任务二"] 这样的 JSON 数组。'
        )

    if not isinstance(raw, list):
        raise ValueError(f"tasks 必须是数组，你发的是 {type(raw).__name__}")

    tasks = [str(item).strip() for item in raw if str(item).strip()]
    if not tasks:
        raise ValueError("tasks 是空的，至少要有一个子任务")

    if len(tasks) > limit:
        raise ValueError(
            f"一次最多派 {limit} 个子任务，你发了 {len(tasks)} 个。"
            f"请合并相近的条目，或只派最重要的 {limit} 个。"
        )
    return tasks


def delegate(tasks: list, max_steps: int = 0) -> str:
    """把多个互相独立的子任务一次性派给并行的子 agent，返回它们各自的结论。

    每个子任务由一个独立的子 agent 负责。它们各有自己的对话上下文，互相看不见，
    也看不到本次对话的历史——所以每条任务描述必须**自包含**：要查什么、查哪个
    范围、要什么口径，全都写在里面，不能出现"上面提到的""刚才那个"这类指代。

    **一条任务只查一个方面，描述写短。** 把四五件事塞进一条描述，子 agent 的步数
    不够用，最后什么都交不出来；描述写太长还会把这一轮回复撑爆，导致整个调用失败。
    要查四个方面就派四条任务。

    拿到结论后由你负责汇总成最终答案。有子任务失败的，要如实说明缺了哪部分。

    Args:
        tasks: 子任务清单，每项是一句自包含的完整描述，例如
            ["查比亚迪 2024 年海外销量与主要市场分布", "查欧盟对中国电动车的关税政策"]
        max_steps: 每个子 agent 最多走几步。不填（0）就用配置里的
            SUBAGENT_MAX_STEPS，硬上限 10。步数乘以子任务数等于本次的总模型
            调用量，别随手调大
    """
    # 延迟 import：见模块 docstring。
    from agent.subagent import format_digest, run_subagents

    limit = int(os.getenv("SUBAGENT_MAX_TASKS", str(_MAX_TASKS)))
    clean = _validate(tasks, limit)

    # 默认值必须在**这里**解析，不能在函数签名里写死。签名里写 5 的话，模型省略这个
    # 参数时 Python 直接填 5 传下去，run_subagents 里的 SUBAGENT_MAX_STEPS 就永远
    # 读不到——那个配置会变成一个改了也没反应的旋钮。0 是「没填」的哨兵值。
    configured = int(os.getenv("SUBAGENT_MAX_STEPS", str(_DEFAULT_STEPS)))
    steps = max(1, min(int(max_steps) or configured, _MAX_STEPS_CEILING))
    results = run_subagents(clean, max_steps=steps)
    return format_digest(results)
