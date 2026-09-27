"""网页搜索工具：调 Tavily 的搜索 API。

刻意只用标准库发请求，不引第三方 SDK——项目里除了 openai 和 python-dotenv
不想再加依赖，而且这一层薄到没什么可省的。

连接是**跨调用复用**的，所以不用 urllib.request.urlopen：那个每次都会新建
连接，而 Tavily 在境外，实测一次 TLS 握手要 1.2 秒（DNS 23ms + TCP 378ms +
TLS 1236ms），同一轮 agent 里搜第二次、第三次等于白送这笔钱。改成模块级持有
一个 HTTPSConnection 之后，只有第一次付握手钱。

单独跑这个模块要先自己 load_dotenv；通过 agent 跑的话，agent/loop.py
导入时已经把 .env 读进来了，所以下面是在**调用时**读环境变量而不是导入时，
否则会读到一个还没加载 .env 的空值。
"""

from __future__ import annotations

import http.client
import json
import os
import time

_HOST = "api.tavily.com"
_PATH = "/search"

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

# 值得重试的服务端状态码。5xx 是对方临时故障，隔一下重发可能就好了。
# **4xx 一律不重试**：401 是 key 不对、429 是额度用完，重发只是白等——
# 这两种模型看不懂但人看得懂，原样透传比吞掉有用。
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_RETRY_BACKOFF = 1.0


# 进程内复用的连接。None = 还没建，或者刚被判定不可用、下次要重连。
_connection: http.client.HTTPSConnection | None = None


def _get_connection(timeout: float) -> http.client.HTTPSConnection:
    """拿到复用的连接，没有就建一个。

    timeout 变了要换新连接——HTTPSConnection 的超时在构造时就写死在 socket 上，
    建完改不了。
    """
    global _connection
    if _connection is not None and _connection.timeout != timeout:
        _connection.close()
        _connection = None
    if _connection is None:
        _connection = http.client.HTTPSConnection(_HOST, timeout=timeout)
    return _connection


def _post_json(payload: dict, api_key: str, timeout: float) -> dict:
    """发一个带 Bearer 鉴权的 JSON POST，返回解析后的响应体。

    换搜索服务商的话，改这个函数和上面的 _HOST / _PATH 就够，web_search 不用动。
    """
    global _connection

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    last_error: RuntimeError | None = None

    # 两次机会，各覆盖一种失败：
    #   连接级 —— 复用的连接随时可能被服务端按 idle 超时单方面关掉，这时第一次
    #             请求会抛 RemoteDisconnected / BadStatusLine（都是 HTTPException）。
    #             丢掉重连就行，不用退避——那是连接过期，不是服务端过载。
    #   5xx   —— 对方临时故障，隔一秒重发一次。
    # 4xx 不在这条路上：服务端已经给了明确答复，重发没有意义。
    for attempt in (1, 2):
        try:
            connection = _get_connection(timeout)
            connection.request("POST", _PATH, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read().decode("utf-8", errors="replace")
        except (http.client.HTTPException, OSError) as exc:
            _connection = None
            last_error = RuntimeError(f"连不上搜索服务：{exc}")
            continue

        if response.status != 200:
            if response.status in _RETRY_STATUSES and attempt == 1:
                time.sleep(_RETRY_BACKOFF)
                last_error = RuntimeError(
                    f"搜索服务返回 HTTP {response.status}：{raw[:300]}"
                )
                continue
            # 4xx 走到这：401 是 key 不对、429 是额度用完，原样透传给模型
            raise RuntimeError(f"搜索服务返回 HTTP {response.status}：{raw[:300]}")

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"搜索服务返回的不是合法 JSON：{raw[:200]}") from exc

    # 两次都没成，把最后一次的失败原样抛出（连接故障或 5xx）
    raise last_error


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

    data = _post_json(payload, api_key, timeout=30.0)
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
