# 阶段二：工具系统小结

阶段二前半（多工具 + 统一接口 + 原生 tool calling）已完成。

| # | 原始待办       | 状态 | 落在哪 |
| --| ---         | --- | --- |
| 1 | tool schema | ✅ | `base.py:94` + `:196` |
| 2 | 参数校验     | ✅ | 值：`coerce`（`base.py:157`）；必填：`required` 发给模型 |
| 3 | tool error  | ✅ | `loop.py:216` |
| 4 | timeout     | ✅ | 三个工具 30s；LLM connect 10s / read 600s（`loop.py:122`） |
| 5 | 重试        | ✅ | 三者失败模式不同，各自实现（见第六节） |
| 6 | 最大循环次数 | ✅ | `loop.py:250` `max_steps=8` |
| 7 | tool result 太长 | ❌ | 只有单条截断 |

---

## 一、三个工具

| | `calculator` | `web_search` | `arxiv_search` |
| --- | --- | --- | --- |
| 签名 | `(expression)` | `(query)` | `(query, max_results=3, recent=False)` |
| 依赖 | 无 | `TAVILY_API_KEY` | 无 |
| 耗时 | 微秒 | 2~9 秒 | 秒级；限流时分钟级 |
| 输出上限 | — | 400 字 × 3 条 + 摘要 | 900 字 × 最多 10 篇 |
| 重试 | — | 连接级 + 5xx | 406/503 退避 + 熔断 |

## 二、加第四个工具

两步：写函数 + `tools/__init__.py:28` 注册一行。没有第三步——schema、提示词、循环都不用动。

```python
# 1. tools/weather.py
def weather(city: str, days: int = 1) -> str:
    """查城市天气预报。

    Args:
        city: 城市名，例如 "上海"
        days: 未来几天，1-7，默认 1
    """
    return "上海今天多云，25~31°C"

# 2. tools/__init__.py
from .weather import weather
TOOLS = {..., "weather": Tool.of(weather)}
```

注意事项：

- **签名和 docstring 就是接口**，模型只看这两个。参数说明会进 schema。
- **函数名 = 工具名**，别起 `search` 这种泛名。
- 子模块名和函数名相同是有意的，代价见 `tools/__init__.py:10-14`：
  `import tools.web_search as ws` 拿到的是**函数**，不是模块。

## 三、schema 从哪来

单一真相来源，全在 `tools/base.py`：

| 产出 | 来源 |
| --- | --- |
| `name` | 函数名 |
| `description` | docstring 第一段（`_parse_docstring:43`） |
| 参数说明 | docstring 的 `Args:` 段 |
| `schema` | `inspect.signature` + `get_type_hints`（`build_schema:94`） |
| `required` | 没有默认值的参数 |

改说明就改 docstring，改参数就改签名，没有第二处要同步。
`openai_spec()`（`:196`）的输出直接就是 `tools=[...]` 要的格式，零适配。

**局限**：`_JSON_TYPES`（`:30`）只认 6 种内建类型，认不出的当 `string`。
所以 `int | None`、`Literal`、`list[str]` 都会退化。当前三个工具够用，写第四个时绕开。

## 四、参数校验

两条路，各管一件事：

| 管什么 | 谁 | 在哪 |
| --- | --- | --- |
| **值**对不对（`"5"` 该是 `5`） | `coerce` | `base.py:157`，本地转换 |
| **必填**项填了没 | `required` | 随 `tools=[...]` 发给模型 |

**值**：`coerce` 按 schema 转类型，转不动就原样放行让函数自己抛（`:129-132`），
不在这层猜默认值。非转不可是因为 `"false"`（字符串）非空即为真，会让 `recent`
排序反掉却**不报错**——静默错误比崩溃难查得多。

**必填**：`required`（`base.py:120`）的消费方是**模型**，不是本地代码。它随
`tools=[...]` 发出去，告诉模型「query 你必须填」。本地不读它是**有意的**：
漏传/多传由 Python 兜底，错误变 Observation 回给模型——

| 模型干了什么 | 模型收到 |
| --- | --- |
| 漏必填参数 | `错误：TypeError: missing 1 required positional argument...` |
| 多给参数 | `错误：TypeError: unexpected keyword argument...` |


## 五、失败兜底

`_run_tool`（`loop.py:216`）四道防线，**全部返回错误文本，无一处 raise**：

| 情况 | 回填内容 |
| --- | --- |
| 工具名不在表里 | `错误：没有名为 X 的工具。可用工具：[...]` |
| 参数不是合法 JSON | 附上原始串 |
| JSON 不是 object | 说明实际类型 |
| 函数抛异常 | `错误：ValueError: ...` |

不抛出去是 ReAct 的核心：错误变 Observation，模型自己纠错。实测 VLN 那次
arxiv 连败 3 次，模型自己换查询词、最后改用 `web_search` 拿到结果，链路没崩。

## 六、重试与超时

| 谁 | 超时 | 重试 |
| --- | --- | --- |
| `calculator` | — | — |
| `web_search` | 30s | 连接级重连一次 + 5xx 隔 1s 重发 |
| `arxiv_search` | 每次请求 30s | 只对 406/503 退避 5s/15s；全败则熔断 |
| `LLM` | connect 10s / read 600s | `max_retries=2`（SDK 默认值，显式写出） |

没抽统一重试层是有意的——三者失败模式完全不同。

**LLM 的超时要分开设**（`loop.py:122`）。一个标量喂不饱两种情况：`read` 在流式下
算的是"两个 token 之间"的间隔，不是生成总时长，所以要宽（600s = 连续 10 分钟
没吐字）；而 `connect` 必须短，端点不通就该 10 秒内失败。写成 `timeout=600`
会把 connect 也变成 600，服务器不可达就干等 10 分钟。

**4xx 不重试是刻意的**（`web_search.py:41`）：401 是 key 不对、429 是额度用完，
重发只会白等，而这两种错误模型看不懂、人看得懂，原样透传更有用。

**arxiv 为什么熔断**（`arxiv_search.py:55`）：一次失败 = 5 + 15 秒退避 + 3 次请求
≈ 20 秒，模型连打 3 次 ≈ 70 秒，产出为零。原因是**封禁粘性**（持续几分钟），
5/15 秒退避跨不过这个量级；而且 9 次请求全在封禁窗口内，**反而刷新了封禁**。
所以退避全败后记一个 180 秒的到期时间，窗口内不再发请求、立刻失败，
错误信息里直接建议模型改走 `web_search`。

## 七、本阶段做的优化

### 连接复用

（`web_search.py:46`、`:49`）原来 `urlopen` 每次新建连接，
而 Tavily 的 TLS 握手实测 1.2 秒。改模块级复用 `HTTPSConnection`：

```
第 1 次: 1.150s
第 2 次: 0.316s       省 0.83 秒/次
```

对照：DeepSeek 握手只要 97ms，而且 SDK 底层 httpx 自带连接池，LLM 本来就不受影响。

### 流式输出

原来非流式（`loop.py:134`）——模型生成 600+ token 期间终端全黑，像卡死。
改成 `stream=True`。

**底座是 SSE（Server-Sent Events），不是「把 JSON 切块发」。** 请求发出后服务端
不一次性返回，而是挂住连接持续写事件块，响应头 `Content-Type: text/event-stream`：

```
data: {"choices":[{"delta":{"content":"你"}}]}

data: {"choices":[{"delta":{"content":"好"}}]}

data: [DONE]
```

纯文本、单向（服务端→客户端）。单向够用——工具结果下一轮开新请求发过去，
不需要在同一条连接上回话。

**SSE 的解析不归我们写。** `openai` SDK 内部实现了它（`openai/_streaming.py`，
头注释写着「最初复制自 httpx-sse」）。所以 `loop.py` 里**看不到任何 SSE 痕迹**——
`stream=True` 之后，拿到手的已经是拼好的 chunk 对象。它顺带改了 `read` 超时的
含义（见第六节）。

**拼装。** 一段回答切成几十上百个 chunk，每个带一个 `delta`：

| 字段 | 怎么拼 | 注意 |
| --- | --- | --- |
| `delta.content` | 字符串累加 | 最简单 |
| `delta.tool_calls` | **按 `index` 归并** | 不能 append，见下 |
| `delta.reasoning_content` | 累加 | 推理型模型把思考放这，`content` 可能空 |


**流式不省时间**，总时长一样，只是把等待变得可见。

## 八、下一步

**下一块工作：上下文管理**（原始第 7 条，阶段二后半）。现状是只增不减——
`web_search` 一次上千字、`arxiv_search` 最坏近 9000 字，`messages` 里只有 `append`，
唯一约束是 `max_steps=8`。`state.py` 当初把 `messages`（给模型）和 `steps`（给人）
拆开就是为了这个：**压 `messages`，留 `steps`**。
