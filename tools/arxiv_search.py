"""arXiv 论文搜索工具。用官方公开 API，不需要 key，也不引入任何第三方依赖。

和 web_search 的两处不同：
  1. 返回的是 Atom XML 不是 JSON，所以要用 xml.etree 解析
  2. arXiv 按 IP 限流，而且手段很硬。

关于限流（实测结论，不是照抄文档）：

  arXiv 要求「每 3 秒最多一个请求」，超了会返回 406。关键在于它的封禁是**粘性**
  的——一旦触发，之后连完全正常的请求也一起被挡，且能持续好几分钟。实测同一个
  URL 连续打，会得到 200 / 406 / 406 / 200 这种自相矛盾的结果，所以「换个 URL
  或换个 HTTP 库就能绕过」是错觉：官方 arxiv.py 包（4.0.1，用 requests + lxml）
  在同一台机器上同样拿到 406。

  因此这里的重点是**请求前就限速**（_MIN_INTERVAL_SECONDS），从源头不踩线；
  下面的退避重试只是补救，救不了已经粘上的封禁。真要快速连查多轮，只能等。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_API_URL = "https://export.arxiv.org/api/query"

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

# arXiv 要求带上能识别调用方的 UA，别用 urllib 默认的
_USER_AGENT = "my_agent/0.1 (learning project; +https://arxiv.org/help/api)"

# 摘要长度上限。arXiv 摘要是搜索结果里最有价值的部分（800 字上下），
# 所以比 web_search 的 400 放宽一倍多，只在超长个例上兜底。
_ABSTRACT_LIMIT = 900

# 请求前强制的最小间隔，对应 arXiv「每 3 秒一个请求」的要求
_MIN_INTERVAL_SECONDS = 3.0

# 撞上限流后的退避。粘性封禁能持续几分钟，所以这里救不了全部情况，
# 只是给偶发的抖动一个机会；真被封死了只能等，错误信息里会说明。
_RETRY_WAITS = (5.0, 15.0)

_last_request_at: float = 0.0


def _text(node: ET.Element, path: str) -> str:
    return (node.findtext(path) or "").strip()


def _fetch(params: dict, timeout: float = 30.0) -> ET.Element:
    """发一次查询并解析 XML。请求前限速，撞上限流再退避重试。"""
    global _last_request_at

    url = f"{_API_URL}?{urllib.parse.urlencode(params)}"
    last_status: int | None = None

    for wait in (0.0, *_RETRY_WAITS):
        if wait:
            time.sleep(wait)

        # 和上一次请求拉开间隔。这是防限流的关键——撞墙之后再退避是被动的，
        # 而且一旦被封就是几分钟起步。
        gap = time.monotonic() - _last_request_at
        if gap < _MIN_INTERVAL_SECONDS:
            time.sleep(_MIN_INTERVAL_SECONDS - gap)
        _last_request_at = time.monotonic()

        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/atom+xml",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return ET.fromstring(response.read())
        except urllib.error.HTTPError as exc:
            # 406 / 503 是 arXiv 的限流表达，值得重试；其他错误重试没意义
            if exc.code in (406, 503):
                last_status = exc.code
                continue
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"arXiv 返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"连不上 arXiv：{exc.reason}") from exc

    raise RuntimeError(
        f"arXiv 限流了（HTTP {last_status}，已按 3 秒间隔重试 {len(_RETRY_WAITS)} 次）。"
        f"它的封禁会持续几分钟，且期间正常请求也会被挡。"
        f"等几分钟再试，或者把查询词写具体一点、少查几次。"
    )


def arxiv_search(query: str, max_results: int = 3, recent: bool = False) -> str:
    """搜索 arXiv 上的学术论文，返回标题、作者、日期、分类和摘要。

    Args:
        query: 搜索词。可以用 arXiv 的字段前缀来精确限定：ti: 标题、au: 作者、
            abs: 摘要、all: 全字段。支持 AND / OR / ANDNOT，例如
            'ti:transformer AND au:vaswani'。不加前缀则按全字段搜
        max_results: 返回几篇，1-10，默认 3。摘要很长，别一次要太多
        recent: True 按提交日期从新到旧排，用于找某个方向的最新进展；
            False（默认）按相关度排，用于找最经典或最相关的论文
    """
    max_results = max(1, min(int(max_results), 10))
    params = {
        "search_query": str(query),
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate" if recent else "relevance",
        "sortOrder": "descending",
    }

    feed = _fetch(params)
    entries = feed.findall(f"{_ATOM}entry")
    if not entries:
        return f"arXiv 上没有找到关于 {query!r} 的论文。"

    total = feed.findtext(f"{_OPENSEARCH}totalResults", "").strip()
    heading = f"arXiv 命中 {total} 篇，以下是前 {len(entries)} 篇：" if total else ""

    blocks: list[str] = []
    for index, entry in enumerate(entries, 1):
        title = " ".join(_text(entry, f"{_ATOM}title").split())

        authors = [
            _text(author, f"{_ATOM}name")
            for author in entry.findall(f"{_ATOM}author")
        ]
        authors = [name for name in authors if name]
        author_line = ", ".join(authors[:3])
        if len(authors) > 3:
            author_line += f" 等 {len(authors)} 人"

        published = _text(entry, f"{_ATOM}published")[:10]
        category = entry.find(f"{_ARXIV}primary_category")
        category = category.get("term", "") if category is not None else ""

        abstract = " ".join(_text(entry, f"{_ATOM}summary").split())
        if len(abstract) > _ABSTRACT_LIMIT:
            abstract = abstract[:_ABSTRACT_LIMIT] + "…"

        meta = " | ".join(part for part in (author_line, published, category) if part)
        blocks.append(
            f"{index}. {title}\n"
            f"   {_text(entry, f'{_ATOM}id')}\n"
            f"   {meta}\n"
            f"   {abstract}"
        )

    body = "\n\n".join(blocks)
    return f"{heading}\n\n{body}" if heading else body
