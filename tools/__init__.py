"""工具注册表。

新增一个工具 = 在 tools/ 下写个函数 + 在下面 TOOLS 里加一条，别处都不用改。
元数据（名字、说明、参数 schema）全部由 Tool.of() 从函数本身推导，
函数怎么写，工具就长什么样。

ReAct 循环只认这张表：to_openai_tools() 把整张表变成 tools=[...] 发给模型，
模型回 tool_calls 后按函数名回表里查，调 tool.run(**kwargs)。

一个坑：子模块名和函数名故意保持相同（web_search.py / web_search），
好处是「工具名 = 文件名 = 函数名」不用记两套，代价是包属性会被
`from .web_search import web_search` 覆盖掉函数——所以
`import tools.web_search as ws` 拿到的是**函数**不是模块。
要用模块本身请走 sys.modules["tools.web_search"]。
"""

from __future__ import annotations

from .arxiv_search import arxiv_search
from .base import Tool
from .calculator import calculator
from .read_file import read_file
from .web_search import web_search

# 无条件注册，哪怕没配 TAVILY_API_KEY。
# 想做「有 key 才注册」在这里是行不通的：agent/loop.py 是先 import tools、
# 后 load_dotenv，所以这个模块执行时 .env 还没加载，os.getenv 一定是空的。
# 没 key 时让 web_search 自己抛错，错误会变成 Observation 回到模型那里。
TOOLS: dict[str, Tool] = {
    "calculator": Tool.of(calculator),
    "web_search": Tool.of(web_search),
    "arxiv_search": Tool.of(arxiv_search),
    "read_file": Tool.of(read_file),
}


def to_openai_tools() -> list[dict]:
    """把整张表转成 chat.completions.create(tools=...) 要的格式。"""
    return [tool.openai_spec() for tool in TOOLS.values()]


__all__ = ["Tool", "TOOLS", "to_openai_tools"]
