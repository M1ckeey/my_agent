"""工具的统一接口。

一个工具 = 一个普通函数。函数的签名和 docstring 是**唯一的真相来源**：

    name        <- 函数名
    description <- docstring 第一段
    参数说明     <- docstring 的 Args: 段
    schema      <- inspect.signature + get_type_hints
    required    <- 没有默认值的那些参数

所以新增工具不用手写任何元数据，写个函数就行。元数据散在两处会漂移，
这里只剩一处——想改说明就改 docstring，想改参数就改签名。

schema 直接就是 OpenAI tool calling 要的 parameters，所以 openai_spec()
不改一个字段就能塞进 tools=[...]，工具这边不用为模型接口做任何适配。

统一入口是 Tool.run()：转换参数 -> 调用函数 -> 结果转字符串。
调用方（agent/loop.py）不需要知道任何工具的细节。
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

# Python 类型 -> JSON Schema 类型。
# 认不出来的一律当 string，宁可传错让函数自己报错，也不要在这一层静默吞掉。
_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

_RE_ARGS_HEADER = re.compile(r"^\s*Args\s*:\s*$")
_RE_ARG_LINE = re.compile(r"^(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """从 Google 风格 docstring 抽出 (一句话描述, {参数名: 说明})。

    只认 Args: 段，Returns: 之类的段落自然被忽略（缩进归零即段落结束）。
    """
    if not doc:
        return "", {}

    lines = inspect.cleandoc(doc).splitlines()

    summary: list[str] = []
    for line in lines:
        if not line.strip():
            break
        summary.append(line.strip())

    param_descs: dict[str, str] = {}
    in_args = False
    last_name: str | None = None
    param_indent: int | None = None

    for line in lines:
        if not in_args:
            if _RE_ARGS_HEADER.match(line):
                in_args = True
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not line[:1].isspace():
            break  # 缩进归零 = Args: 段结束

        indent = len(line) - len(line.lstrip())

        # 比参数行缩进更深的，是上一条说明的续行。
        # 必须先按缩进判断再试正则——续行本身可能长得很像参数行
        # （比如 "abs: 摘要、all: 全字段" 就会被误认成名叫 abs 的参数）。
        if param_indent is not None and indent > param_indent:
            if last_name:
                param_descs[last_name] = f"{param_descs[last_name]} {stripped}"
            continue

        match = _RE_ARG_LINE.match(stripped)
        if match:
            param_indent = indent
            last_name = match.group(1)
            param_descs[last_name] = match.group(2).strip()

    return " ".join(summary), param_descs


def build_schema(func: Callable[..., Any], param_descs: dict[str, str]) -> dict:
    """从函数签名生成 JSON Schema。

    这个 dict 同时服务两个消费方，所以格式就按 JSON Schema 来：
      1. 作为 OpenAI tool calling 的 parameters 发给模型
      2. 执行前按 type 转换模型给的参数

    不写 default：可选参数的真正默认值在函数签名里，模型省略时由 Python 生效，
    schema 里再写一遍是重复的真相来源（而且 OpenAI 的 parameters 也不认它）。
    """
    hints = get_type_hints(func)
    signature = inspect.signature(func)

    properties: dict[str, dict] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        # *args / **kwargs 在 ReAct 里没法用文本表达，跳过
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue

        prop: dict = {"type": _JSON_TYPES.get(hints.get(name, str), "string")}
        if name in param_descs:
            prop["description"] = param_descs[name]

        if param.default is inspect.Parameter.empty:
            required.append(name)

        properties[name] = prop

    return {"type": "object", "properties": properties, "required": required}


def _coerce_one(value: Any, expected: str | None) -> Any:
    """按目标类型转一个值，转不动就原样返回。

    原样返回是有意的：让函数自己抛 TypeError，走「错误变 Observation」那条路，
    比在这一层猜一个默认值更容易让模型看懂哪里错了。
    """
    if isinstance(value, bool):
        # bool 是 int 的子类，得先拦掉，否则 True 会被当成 1
        return value

    if expected == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "是"}:
            return True
        if lowered in {"false", "0", "no", "否"}:
            return False
        return value

    try:
        if expected == "integer":
            return int(value)
        if expected == "number":
            return float(value)
    except (TypeError, ValueError):
        return value
    if expected == "string" and not isinstance(value, str):
        return str(value)
    return value


def coerce(schema: dict, kwargs: dict) -> dict:
    """按 schema 把参数转成函数期望的类型。

    模型从 JSON 里给的值类型经常不对，最常见的是该给数字却给了字符串：
        {"max_results": "5"}   ->   5
    """
    properties = schema.get("properties", {})
    return {
        name: _coerce_one(value, properties.get(name, {}).get("type"))
        for name, value in kwargs.items()
    }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable[..., Any]

    @classmethod
    def of(cls, func: Callable[..., Any]) -> "Tool":
        """从一个普通函数造出 Tool，元数据全部推导，不用手写。"""
        summary, param_descs = _parse_docstring(func.__doc__)
        return cls(
            name=func.__name__,
            description=summary or func.__name__,
            schema=build_schema(func, param_descs),
            func=func,
        )

    def run(self, **kwargs) -> str:
        """统一入口：转换参数 -> 调用函数 -> 结果转字符串。

        异常不在这里吞。loop 会捕获它并把错误内容作为 tool 消息丢回给模型，
        错误信息的措辞是提示词的一部分，属于循环那一层。
        """
        return str(self.func(**coerce(self.schema, kwargs)))

    def openai_spec(self) -> dict:
        """转成 chat.completions.create(tools=[...]) 里的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }
