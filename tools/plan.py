"""plan_research：把研究主题拆成互相独立的子问题，并判断该不该派子 agent。

这是「搜索」和「研究」的分界线。不拆的话模型面对一个大题目只会随便搜一两次就
动笔，写出来的东西很浅——瓶颈从来不是搜不到，是**不知道该搜哪个角度**。

拆出来之后还有一个同样重要的用处：它给出了「该查哪几个方面」的清单，后面聚合
时对照这份清单就知道漏了哪块，而不是凭感觉说「查得差不多了」。

这个模块**不在模块级 import agent.***——`agent/__init__.py` 会立刻
`from .loop import run_agent`，而 loop 又 `from tools import ...`，模块级相互
import 会绕成环。所以 agent 侧的东西一律在函数体内延迟 import。

提示词从 `prompts` 拿。那个模块在项目根目录、不 import 任何本项目模块，所以
可以安全地在模块级 import——这也是它没被放进 agent/ 的原因。
"""

from __future__ import annotations

import json
import os
import re

from prompts import PLANNER_SYSTEM

# 默认最多拆几个子问题。这个值**不再写进提示词**——它是代码侧的截断线：模型给的
# 子问题超出就按重要性从尾部砍掉，并在计划末尾写明砍了几个。
# 曾经把数字写进提示词（「超过 N 个的部分会被系统丢弃」），结果模型把它当配额，
# 每次刚好凑满 N 个：上限设 2 就 2 个、设 8 就 8 个，两个主题五个上限 10/10 命中。
# 想省钱要动的是 SUBAGENT_MAX_TASKS——调小这里不会让模型拆得更省，只会白丢信息。
_DEFAULT_MAX = 5

# 报错时回显多少原始输出。这个值只影响错误信息的长短——回显得太少，模型看不懂
# 自己错在哪；回显得太多，又把主上下文塞满了。它在 Observation 里是要占地方的。
_ERROR_LIMIT = 600

# 内容层重试次数。call_llm 已经重试过传输层（连接断了、超时），但有一种失败它看不到：
# 服务端返回 200、内容却被提前截断，既不抛异常也没有任何标志位——实测一次只回了 16 个
# 字符 `{\n  "complexity`，20 次调用里出过一次。这种只能在下游解析时才发现，所以
# 重试挂在 _extract_json 上。重的是**内容**不是**传输**，和 call_llm 那层不是一回事。
_PLAN_ATTEMPTS = 2

# 提示词在 prompts.py 的 PLANNER_SYSTEM。那里**故意不写任何数字**——原因见上面
# _DEFAULT_MAX 的注释。注意它现在用 .format() 会炸（JSON 里是裸花括号），
# 不要再往里塞 {max_n} 之类的占位符。


def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象。

    模型经常不听话：裹在 ```json 代码块里、前面加一句"好的，以下是计划"、
    或者结尾追一段说明。

    用 raw_decode 而不是「第一个 { 到最后一个 }」：后者在模型于 JSON 之后又写了
    一句话（里面恰好带 }）时，会把两段无关文本缝在一起，报出一个完全看不懂的
    语法错误。raw_decode 只吃第一个完整的 JSON 对象，后面多出来的东西自动忽略。
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        raise ValueError(f"输出里没有 JSON 对象：{text[:_ERROR_LIMIT]!r}")

    try:
        data, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        # 把原始输出一起抛出去：这个错误最终会变成模型的 Observation，
        # 它需要看到自己到底写坏了什么才能改对
        raise ValueError(f"输出的 JSON 不合法：{exc}。原始输出：{text[:_ERROR_LIMIT]!r}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层应该是对象，实际是 {type(data).__name__}")
    return data


def _clean_questions(raw: object, limit: int) -> list[dict]:
    """规范化子问题列表：丢掉空问题、补 id、去重 id、截到上限。

    去重 id 不是洁癖：delegate 之后每个子任务是一个独立的子 agent，id 撞了会让人
    分不清哪份结论对应哪一条。空问题则根本没法派出去。
    """
    items = raw if isinstance(raw, list) else []
    result: list[dict] = []
    used: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        sq_id = str(item.get("id", "")).strip() or f"q{len(result) + 1}"
        if sq_id in used:
            # 撞号了就另找一个没被占的。后缀必须**单调递增**——原来的写法拿
            # len(result) 和 len(used) 当候选，这两个值在循环体内都不变，所以一旦
            # 两个候选都已被占，while 就原地打转成死循环。实测能挂死的输入：
            # 三个子问题的 id 依次给 "q3"、"q3_2"、"q3"（第二个 q3 进来时 used 里
            # 正好同时有 q3 和 q3_2，且 len(result) 与 len(used) 都是 2）。
            base = f"q{len(result) + 1}"
            sq_id = base
            suffix = 2
            while sq_id in used:
                sq_id = f"{base}_{suffix}"
                suffix += 1
        used.add(sq_id)
        result.append(
            {
                "id": sq_id,
                "question": question,
                "rationale": str(item.get("rationale", "")).strip(),
            }
        )

    return result[:limit]


def _render(topic: str, subquestions: list[dict], complex_plan: bool, truncated: int) -> str:
    """把计划排成给模型看的文本。

    这里显式写出「建议怎么做」而不是只丢一个清单：下一步该调 delegate 还是自己
    查，是这个工具唯一能判断、而模型看不到的判断——它不知道子问题之间有没有依赖。
    """
    verdict = "复杂" if complex_plan else "简单"
    lines = [f"研究计划：{topic}", f"子问题 {len(subquestions)} 个，判定：{verdict}", ""]

    for i, sq in enumerate(subquestions, 1):
        lines.append(f"{i}. [{sq['id']}] {sq['question']}")
        if sq["rationale"]:
            lines.append(f"   理由：{sq['rationale']}")

    if truncated:
        lines.append(f"（已按重要性从尾部丢弃 {truncated} 个价值较低的子问题）")

    lines.append("")
    if complex_plan:
        lines.append(
            "建议：用一次 delegate 把它们并行派发——每个子问题对应一条任务，"
            "一条只查一个方面，描述写短（一两句）但**自包含**：子 agent 看不到彼此，"
            "也看不到本次对话。"
        )
    else:
        lines.append("建议：子问题少且互不独立，自己按顺序用检索工具查即可，不必派子 agent。")
    return "\n".join(lines)


def plan_research(topic: str) -> str:
    """把一个研究主题拆成互相独立的子问题，并判断该不该派子 agent 并行调研。

    遇到需要调研、比较、综述多个方面的问题，先调这个工具。它会给出子问题清单和
    研究顺序；如果判定为复杂，下一步就用 delegate 把子问题并行派出去。

    简单的计算、单次查找不要调这个工具，直接作答更快。

    Args:
        topic: 要研究的主题，一句话讲清楚，例如 "国产新能源车企出海的现状与壁垒"
    """
    # 延迟 import：见模块 docstring。tools 在 import 时 agent.loop 可能只加载了一半。
    from agent.llm import call_llm, final_text, get_client

    topic = str(topic).strip()
    if not topic:
        raise ValueError("topic 不能为空")

    limit = int(os.getenv("PLAN_MAX_SUBQUESTIONS", str(_DEFAULT_MAX)))

    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    # 计划有一份 JSON，比 ReAct 的一步长得多，1024 会把它拦腰截断成非法 JSON
    max_tokens = int(os.getenv("PLAN_MAX_TOKENS", "2048"))
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": f"研究主题：{topic}"},
    ]

    # 内容层重试，见 _PLAN_ATTEMPTS 的注释。解析失败就追加一句纠错再要一次——
    # 把原始输出留在对话里而不是重开一轮，模型能看到自己上次写坏在哪。
    for attempt in range(1, _PLAN_ATTEMPTS + 1):
        message = call_llm(
            get_client(),
            model,
            messages,
            tools=[],  # 规划不需要工具，纯生成
            max_tokens=max_tokens,
        )
        try:
            data = _extract_json(final_text(message))
            break
        except ValueError:
            if attempt == _PLAN_ATTEMPTS:
                raise
            messages.append({"role": "assistant", "content": final_text(message)})
            messages.append(
                {
                    "role": "user",
                    "content": "上一条回复的 JSON 不完整或格式不对，请重新输出一份完整、"
                    "合法的 JSON，不要省略、不要截断。",
                }
            )

    subquestions = _clean_questions(data.get("subquestions"), limit)
    if not subquestions:
        raise ValueError(f"planner 没有产出可用的子问题，原始输出：{final_text(message)[:_ERROR_LIMIT]!r}")

    # complexity 由模型判定，但缺字段/写错值时按子问题数兜底——
    # 这个判断的后果是「多花钱」还是「少查一层」，不该完全交给模型的心情。
    raw_verdict = str(data.get("complexity", "")).strip().lower()
    complex_plan = raw_verdict == "complex" if raw_verdict in {"simple", "complex"} else len(subquestions) >= 3

    total = len(data.get("subquestions") or []) if isinstance(data.get("subquestions"), list) else 0
    return _render(topic, subquestions, complex_plan, max(0, total - len(subquestions)))
