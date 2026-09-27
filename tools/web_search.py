"""网页搜索工具：调 Tavily 的搜索 API。

刻意只用标准库 urllib 发请求，不引第三方 SDK——项目里除了 openai 和
python-dotenv 不想再加依赖，而且这一层薄到没什么可省的。

单独跑这个模块要先自己 load_dotenv；通过 agent 跑的话，agent/loop.py
导入时已经把 .env 读进来了，所以下面是在**调用时**读环境变量而不是导入时，
否则会读到一个还没加载 .env 的空值。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_API_URL = "https://api.tavily.com/search"

# 每条结果的内容片段上限。实测 basic 搜索每条内容都超过 400 字（Tavily 文档
# 写的是 200-300 字，与实际不符），所以这个上限**每次都会触发**——它是真在
# 起作用的旋钮，不是安全网。调大直接换更多上下文。
# 但根子不在这：搜索结果整段追加进 messages 且永不清理，一次搜索就上千字，
# 搜几次上万（见 README「已知限制」）。那是阶段二上下文压缩要解决的。
_SNIPPET_LIMIT = 400

# 返回几条结果。**写死，不暴露给模型**——和下面 payload 里的 search_depth 同一个
# 理由：模型看不到额度、看不到延迟，没有判断该要几条的依据，描述怎么写都会诱导
# 它往多了要。想改就改这里（Tavily 只接受 1-10）。
_MAX_RESULTS = 3


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    """发一个带 Bearer 鉴权的 JSON POST，返回解析后的响应体。

    换搜索服务商的话，改这个函数和上面的 _API_URL 就够，web_search 不用动。
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 把服务端的错误说明带出来——401 是 key 不对，429 是额度用完，
        # 这两种模型看不懂但人看得懂，值得原样透传
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"搜索服务返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连不上搜索服务：{exc.reason}") from exc


def web_search(query: str) -> str:
    """搜索网页，返回结果摘要和来源链接。需要最新信息或你不确定的事实时用它。

    Args:
        query: 搜索关键词。写具体一点，"Python 3.13 新特性" 比 "Python" 好得多
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 TAVILY_API_KEY。到 https://tavily.com 注册拿 key（免费额度 1000 次/月），"
            "填进项目根目录的 .env 里那一行 TAVILY_API_KEY="
        )

    payload = {
        "query": str(query),
        "max_results": _MAX_RESULTS,
        # 显式写死 basic（1 额度）。advanced 是 2 额度、慢 5-10 秒、每条内容
        # 长 5-20 倍，而模型没有判断该不该用它的依据——真需要更深的内容，
        # 换个更精确的查询词再搜一次更划算。所以这个参数不暴露给模型。
        "search_depth": "basic",
        "include_answer": True,
    }

    data = _post_json(_API_URL, payload, api_key, timeout=30.0)
    if not isinstance(data, dict):
        raise RuntimeError(f"搜索服务返回了意外的格式：{str(data)[:200]}")

    answer = (data.get("answer") or "").strip()
    results = data.get("results") or []
    if not answer and not results:
        return f"没有找到关于 {query!r} 的结果。"

    blocks: list[str] = []
    if answer:
        blocks.append(f"摘要：{answer}")

    for index, item in enumerate(results, 1):
        title = (item.get("title") or "").strip() or "(无标题)"
        url = (item.get("url") or "").strip()
        content = " ".join((item.get("content") or "").split())
        if len(content) > _SNIPPET_LIMIT:
            content = content[:_SNIPPET_LIMIT] + "…"
        blocks.append(f"{index}. {title}\n   {url}\n   {content}")

    return "\n\n".join(blocks)
