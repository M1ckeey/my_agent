"""工具注册表。

新增一个工具 = 在 tools/ 下写个函数 + 在下面 TOOLS 里加一条，别处都不用改。
元数据（名字、说明、参数 schema）全部由 Tool.of() 从函数本身推导，
函数怎么写，工具就长什么样。

ReAct 循环只认这张表：to_openai_tools() 把整张表变成 tools=[...] 发给模型，
模型回 tool_calls 后按函数名回表里查，调 run_tool() 执行。

一个坑：子模块名和函数名故意保持相同（web_search.py / web_search），
好处是「工具名 = 文件名 = 函数名」不用记两套，代价是包属性会被
`from .web_search import web_search` 覆盖掉函数——所以
`import tools.web_search as ws` 拿到的是**函数**不是模块。
要用模块本身请走 sys.modules["tools.web_search"]。

另一个坑（加 delegate 时踩到的）：**tools/ 下的模块不能在模块级 import agent.***。
agent/__init__.py 会立刻 `from .loop import run_agent`，而 loop 又 `from tools import ...`
——模块级相互 import 会绕成环，import 到一半就炸。所以 plan.py / delegate.py
里对 agent 侧的依赖一律写在函数体内。
"""

from __future__ import annotations

import json
from collections.abc import Collection

from .arxiv_search import arxiv_search
from .base import Tool
from .calculator import calculator
from .compact import compact
from .delegate import delegate
from .plan import plan_research
from .read_file import read_file
from .web_search import web_search

# 无条件注册，哪怕没配 TAVILY_API_KEY。
# 想做「有 key 才注册」在这里是行不通的：agent/loop.py 是先 import tools、
# 后 load_dotenv，所以这个模块执行时 .env 还没加载，os.getenv 一定是空的。
# 没 key 时让 web_search 自己抛错，错误会变成 Observation 回到模型那里。
TOOLS: dict[str, Tool] = {
    "calculator": Tool.of(calculator),
    "compact": Tool.of(compact),
    "web_search": Tool.of(web_search),
    "arxiv_search": Tool.of(arxiv_search),
    "read_file": Tool.of(read_file),
    "plan_research": Tool.of(plan_research),
    "delegate": Tool.of(delegate),
}


def to_openai_tools(only: Collection[str] | None = None) -> list[dict]:
    """把整张表转成 chat.completions.create(tools=...) 要的格式。

    Args:
        only: 只要这几个工具。子 agent 走这条——它的工具集必须是主 agent 的子集，
            尤其是**不能包含 delegate**，否则子 agent 可以再派子 agent，调用量
            按 fan-out 的幂次涨。传 None 就是全表。
    """
    names = list(TOOLS) if only is None else list(only)
    return [TOOLS[name].openai_spec() for name in names if name in TOOLS]


def run_tool(name: str, arguments: str) -> str:
    """按名字执行一次工具调用，返回要回填给模型的文本。

    从 agent/loop.py 搬过来的：子 agent 也要执行工具，放在这里两边共用一个实现。

    任何异常都变成返回内容而不是向上抛——模型看到错误信息能自己纠错，
    这是 ReAct 里「把错误当观察」的全部意义。

    Args:
        name: 工具名。
        arguments: 模型给的参数，一个 JSON 字符串（空串按「无参数」处理）。
    """
    tool = TOOLS.get(name)
    if tool is None:
        return f"错误：没有名为 {name!r} 的工具。可用工具：{list(TOOLS)}"

    try:
        # 模型给的是一个 JSON 字符串；空串按「无参数」处理
        kwargs = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return f"错误：工具参数不是合法 JSON：{exc}。你发的是 {arguments!r}"

    if not isinstance(kwargs, dict):
        return f"错误：工具参数必须是一个 JSON 对象，你发的是 {type(kwargs).__name__}"

    try:
        return tool.run(**kwargs)
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


__all__ = ["Tool", "TOOLS", "run_tool", "to_openai_tools"]
