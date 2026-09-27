# 阶段二 TODO 状态

原始清单 7 条，对照代码核对（2026-09-27）：**3 项完成、3 项做了一半、1 项没动。**

| # | 项 | 状态 | 落在哪 |
| --- | --- | --- | --- |
| 1 | tool schema | ✅ 完成 | `tools/base.py:94` `build_schema` + `:196` `openai_spec` |
| 2 | 参数校验 | ⚠️ 一半 | 类型转换有，required 校验没有 |
| 3 | tool error | ✅ 完成 | `agent/loop.py:105-131` 四道防线 |
| 4 | timeout | ⚠️ 一半 | 两个工具都有 30s，**LLM 调用没有** |
| 5 | 重试 | ⚠️ 一半 | arXiv 有，web_search 没有 |
| 6 | 最大循环次数 | ✅ 完成 | `agent/loop.py:139` `max_steps=8` + `:216` 兜底 |
| 7 | tool result 太长怎么办 | ❌ 没做 | 只有单条截断，没有总量控制 |

这 7 条属于阶段二的前半（多工具 + 统一接口），和 README 阶段路线表自洽：
前半已收尾，剩下第 7 条是后半「上下文压缩」的整块工作。

---

## ✅ 1. tool schema

元数据从函数本身推导，`tools/base.py` 一个文件解决：

- `build_schema()`（`base.py:94-124`）用 `inspect.signature` + `get_type_hints` 生成 JSON Schema，
  没有默认值的参数进 `required`
- `_parse_docstring()`（`base.py:43-91`）抽 Google 风格 `Args:` 段，
  续行靠缩进判断（否则 `abs: 摘要` 这种会被误认成参数名）
- `openai_spec()`（`base.py:196-205`）的输出**一个字段不改**就能塞进 `tools=[...]`，
  工具层不为模型接口做任何适配

新增工具 = 写函数 + `tools/__init__.py:28` 加一行。

## ✅ 3. tool error

完成度最高的一项。`_run_tool`（`agent/loop.py:105-131`）四道防线，**全部返回错误文本，无一处 raise**：

| 情况 | 回填给模型 |
| --- | --- |
| 工具名不在 `TOOLS` 表 | `错误：没有名为 X 的工具。可用工具：[...]` |
| `arguments` 不是合法 JSON | 附上它自己发的原始串 |
| JSON 但不是 object | 说明类型 |
| 函数抛异常 | `错误：ValueError: ...` |

设计意图写在 `loop.py:105-110` 和 README 设计决策 #3：错误变 Observation，
模型看到能自己换做法重试，循环不会因一次搜索超时整体崩掉。
`Tool.run` 特意不吞异常（`base.py:189-193`），因为错误信息的措辞属于循环那一层。

## ✅ 6. 最大循环次数

`max_steps: int = 8`（`loop.py:139`），语义是「一次模型调用算一步」。
跑满不 break 时由 `for-else` 兜底（`loop.py:214-216`），把 final_answer 设成
「（达到最大步数 8，任务未完成）」，而不是抛异常。

遗留小缺口：写死在函数签名默认值里，`.env` 没有对应项，且 `main.py:31` 从不传参，
所以实际永远是 8。改起来不难，但目前也没有非改不可的理由。

---

## ⚠️ 2. 参数校验

**`required` 生成了，但没人读。** grep 全项目，`required` 只出现在
`base.py:108 / 120 / 124` —— 只被构造、从没被消费。它作为 schema 的一部分发给了模型
（模型能看见哪些必填），但本地不校验。

`coerce`（`base.py:157-167`）只遍历 `kwargs` 里**已有**的键，所以：

- 模型漏必填参数 → `self.func(**kwargs)`（`base.py:194`）抛
  `TypeError: missing 1 required positional argument` → 被 `loop.py:130` 接住变 Observation
- 模型多给参数 → 同样 `TypeError: unexpected keyword argument`

有反馈、不会崩，但模型收到的是 Python 的 TypeError 原文。

转换那半做得挺细：`"5"`→`5`、`"true"`→`True`，还专门排掉了 `bool` 是 `int` 子类这个坑
（`base.py:133-136`）；转不动就原样放行让函数自己抛，不在这一层猜默认值。

**待定**：把 `required` 用起来（执行前显式校验、给中文错误），
还是干脆从 schema 里删掉、承认校验交给 Python。

## ⚠️ 4. timeout

两个工具都传了 30 秒（`web_search.py:82`、`arxiv_search.py:54`），
但 `_call_llm`（`loop.py:69-93`）里那个 `client.chat.completions.create(**kwargs)`
**没有任何 timeout 参数** —— 裸奔的恰恰是 LLM。

实测本机 openai 2.53.0 的默认值：

```
DEFAULT_TIMEOUT     = Timeout(connect=5.0, read=600, write=600, pool=600)
DEFAULT_MAX_RETRIES = 2
```

`read=600` = 一次读超时 **10 分钟**，SDK 还会自己重试 2 次 → 最坏一次 `run_agent` 卡半小时。

**这是 7 项里最值得先修的一条**，改一行的事：

```python
OpenAI(api_key=..., base_url=..., timeout=..., max_retries=...)
```

另一个更隐的缺口：**没有「单次工具调用的总预算」**。
`arxiv_search._fetch` 最坏 = 限速 3s + 退避 5s + 15s + 三次 30s 网络超时 ≈ 2.5 分钟，
这个时间没被任何外层约束。

## ⚠️ 5. 重试

**arXiv 做得最讲究**（`arxiv_search.py:40-96`）：

- 请求**前**限速 3 秒（`_MIN_INTERVAL_SECONDS`），从源头躲开限流
- 只对 406/503 退避重试（`_RETRY_WAITS = (5.0, 15.0)`），其他错误不重试
- 最终错误信息把「arXiv 封禁是粘性的、只能等几分钟」讲给模型听（`:92-96`）

**web_search 一次失败就抛**（`web_search.py:47-56`），429 和 5xx 都不重试。
429 不重试是对的（额度用完，重试没意义），5xx 值得补。

**LLM 调用零配置** —— `max_retries` 全项目零出现。
现在享受的 2 次重试是 SDK 默认行为，不是项目的决定。

**待定**：要不要抽一层统一的重试策略，还是维持「各工具自己按业务决定」的现状。
后者其实更贴合这几个工具各自的失败模式，倾向保留。

---

## ❌ 7. tool result 太长怎么办

**唯一完全没动的，也是 README 自己点名的最大问题。**

现状是「每条截断，总量不管」：

- `web_search`：`_SNIPPET_LIMIT = 400` × 3 条 + answer → 一次搜索上千字
- `arxiv_search`：`_ABSTRACT_LIMIT = 900` × 最多 **10** 篇 → 最坏近 9000 字

而 `messages` 只增不减 —— `loop.py:180` 和 `:201` 只有 `append`，全文件没有任何裁剪逻辑，
唯一的间接约束是 `max_steps=8`。

责任归属代码里写得很清楚，`web_search.py:23-25`：

> 搜索结果整段追加进 messages 且永不清理，一次搜索就上千字，搜几次上万
> （见 README「已知限制」）。那是阶段二上下文压缩要解决的。

对应 README 阶段路线表「上下文压缩 / 长期记忆 = 待开始」。
**这是下一个阶段的整块工作，不适合顺手做。**

---

## 📌 性能优化（已选中，待做）

### web_search 复用连接

单次调用的固定成本实测（2026-09-27，本机）：

| 服务 | DNS | TCP | TLS | 合计 |
| --- | --- | --- | --- | --- |
| `api.deepseek.com` | 24 ms | 25 ms | 48 ms | **97 ms** |
| `api.tavily.com` | 23 ms | 378 ms | **1236 ms** | **1637 ms** |
| `export.arxiv.org` | 17 ms | 1252 ms | 602 ms | 1871 ms |

Tavily 的 TLS 握手单独就 1.24 秒。而 `_post_json` 用的是
`urllib.request.urlopen`（`web_search.py:48`），**每次新建连接、不复用**——
这 1.6 秒每次全额支付，还没开始传数据就先花掉。

改法：模块级持有一个 `http.client.HTTPSConnection` 跨调用复用，出错时重连。
纯标准库，不引第三方依赖（符合 `web_search.py:3-4` 的原则）。

顺带两点：
- openai SDK 底层是 httpx，自带连接池，所以 **LLM 调用不受此影响**（97ms 只付一次）
- Tavily 的 `include_answer: True`（`web_search.py:79`）会让**服务端多跑一遍 LLM**
  生成摘要才返回，这是握手之外的另一层固定延迟，是否保留可以另议

### 流式输出

`_call_llm`（`loop.py:92`）用的是非流式 `create()`，必须等整段生成完才返回。
VLN 那题最终答案 600+ token，模型在逐字生成，但终端**一个字都不显示**——
它一直在工作，只是看不见。这是「体感卡住」的主因。

改法：`stream=True`，边生成边打印。

**一个坑要先想好**：`_print_step` 现在会把 `content` 当 Thought 再打一遍
（`loop.py:198` 取 content、`:223` 再 print），流式之后同一段文字会被打两次。
要么流式时不逐字打 Thought、要么打完就跳过 `_print_step` 的 Thought 那段。

---

## 建议的修复顺序

1. **web_search 复用连接** —— 每次省 ~1.6 秒，纯标准库
2. **流式输出** —— 不省时间，但消除「卡住」的体感
3. **`_call_llm` 配 timeout + max_retries** —— 一行，收益最大
4. **`required` 用起来或删掉** —— 消除「生成但没人读」的死代码
5. **第 7 项** —— 属于阶段二后半，单独开工
