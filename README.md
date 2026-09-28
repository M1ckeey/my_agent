# my_agent

从零手搓一个 Agent。

**当前阶段：第二阶段 —— 多工具 + 统一工具接口 + 原生 tool calling**

不依赖 LangChain / LlamaIndex，只用 `openai` SDK 和自己写的循环，
把「智能体到底在干什么」这件事拆到看得见每一行。

---

## 阶段路线

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| **一** | 最小 ReAct 循环：Thought → Action → Observation，接一个计算器 | ✅ 完成 |
| **二** | 多工具 + 工具选择，工具统一接口，换成原生 tool calling | ✅ 完成 |
| **三** | 子 agent：`plan_research` 拆题 + `delegate` 并行派发 | ✅ 完成 |
| 二 | 上下文压缩 / 长期记忆 | 待开始 |
| 四 | 规划与反思（Plan-and-Solve、自我纠错） | 待开始 |
| 五 | 真正意义上的多智能体协作 | 待开始 |

阶段二原本是一整块，现在拆成两行：多工具那半做完了，压缩那半没动。

**但子 agent（阶段三）先落地了，因为它本身就替代了压缩机制里收益最大的那一块**：
子 agent 的工具结果根本不进主上下文——实测一次调研子 agent 侧产出 17.9 KB，
进主上下文的只有四条结论。1.md 那套四步压缩里，前三步（转存 / 截断 / 替换旧结果）
都是为「工具结果在上下文里堆积」服务的，这个前提被改掉了一部分。

真正还需要压缩的只剩两处：主 agent 自己查的那几轮，以及**子 agent 自己的上下文**
（跑 5 步、一次搜索 2665 字，它也会满）——最后这条是新出现的，以前只有一层上下文
要管，现在有两层。

---

## 快速开始

```bash
pip install -r requirements.txt     # 只有 openai 和 python-dotenv 两个依赖

# 填 key：打开项目根目录的 .env
#   LLM_API_KEY      必填，用 LLM 的
#   TAVILY_API_KEY   想让 web_search 能用就填，不填的话只有它不可用
# 其余都有默认值

python main.py "123 * 456 再减去 1000 等于多少"
python main.py "搜索一下 Python 3.13 有哪些新特性"
python main.py                        # 不带参数进交互模式
```

`.env` 由 `python-dotenv` 在 `agent/loop.py` 导入时加载，
显式锚定项目根目录，所以**从任何目录启动都能读到**，也不需要重启终端。
改完 `.env` 直接重跑即可。

真实环境变量优先级高于 `.env`，临时换个 key 直接 `export` 覆盖就行。

> ⚠️ 一个坑：只有走 `main.py` / `run_agent` 才会加载 `.env`。
> 单独 `from tools import ...` 写脚本时 `.env` 是没被读的，得自己先
> `load_dotenv()`。原因是 `agent/loop.py` **先 import tools、后 load_dotenv**。
> 这也是为什么各工具都在**调用时**读环境变量，而不是在导入时。

### 配置项（都在 `.env` 里）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | — | **必填**，留空启动时会提示 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 换模型只改这个 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_TEMPERATURE` | `0` | 设为**空字符串**则不发送该参数（推理型模型会拒绝 temperature） |
| `LLM_MAX_TOKENS` | `4096` | 单次回复上限。加了 delegate 之后别调小，见下面的警告 |
| `LLM_CONNECT_TIMEOUT` | `10` | 端点不通就该 10 秒内失败，别干等 |
| `LLM_READ_TIMEOUT` | `600` | 流式下算的是**两个 token 之间**的间隔，不是总时长 |
| `LLM_MAX_RETRIES` | `2` | SDK 自带的层：只重试连接层错误和 429/5xx，不重试「答了但格式不对」。**只覆盖约 1.5 秒**，别指望它兜住长抖动 |
| `LLM_RETRIES` | `3` | 我们自己在外面套的一层，只重试连接/超时，退避 1s/2s/4s |
| `LLM_RETRY_DELAY` | `1.0` | 上面那层的退避基准，第 n 次等 `× 2^n` 秒 |
| `TAVILY_API_KEY` | — | `web_search` 用，留空则该工具报错 |

因为是 OpenAI 兼容协议，`LLM_BASE_URL` 一改就能换到 Qwen / Kimi / GLM / 本地 vLLM / Ollama。
`.env` 里已经把这四家的配置注释好了，取消注释即可切换。

> `.env` 已在 `.gitignore` 里，不会被提交。里面放的是密钥。

---

## 目录结构

```
my_agent/
├── main.py                # 入口：命令行参数 / 交互模式 / Windows GBK 控制台修复
├── .env                   # 配置 + 密钥（已在 .gitignore 里）
├── requirements.txt
├── step1.md               # 阶段一小结：ReAct 循环 + calculator
├── step2.md               # 阶段二小结：工具系统
├── step3.md               # 阶段三小结：子 agent
├── prompts.py             # ★ 三份 system 提示词（放根目录是为了避开循环导入）
├── agent/
│   ├── loop.py            # ★ 主循环（只管控制流）
│   ├── llm.py             # ★ LLM 调用层：客户端单例 + 流式拼装（主/子 agent 共用）
│   ├── subagent.py        # ★ 子 agent：上下文隔离的独立循环 + 并行 fan-out
│   └── state.py           # ★ 运行时状态：messages（给模型）+ steps（给人看）
└── tools/
    ├── base.py            # ★ 工具统一接口：Tool 类 + schema 推导 + 类型转换 + OpenAI 规格
    ├── __init__.py        # 工具注册表：TOOLS + to_openai_tools() + run_tool()
    ├── calculator.py      # 计算器（ast 白名单求值，不用 eval）
    ├── web_search.py      # 网页搜索（Tavily API）
    ├── arxiv_search.py    # arXiv 论文搜索（公开 API，无需 key）
    ├── read_file.py       # 读文件（限项目目录内，带行号分页）
    ├── plan.py            # 拆题：主题 → 子问题清单 + 复杂度判断
    └── delegate.py        # 派活：子任务 → 并行子 agent → 各自结论
```

---

## 工具系统

### 一个工具 = 一个普通函数

新增工具只需要两处改动：

```python
# 1. tools/web_search.py —— 写个函数
def web_search(query: str) -> str:
    """搜索网页，返回结果摘要和来源链接。需要最新信息或你不确定的事实时用它。

    Args:
        query: 搜索关键词。写具体一点，"Python 3.13 新特性" 比 "Python" 好得多
    """
    ...

# 2. tools/__init__.py —— 注册一行
TOOLS = {
    "calculator": Tool.of(calculator),
    "web_search": Tool.of(web_search),
}
```

不用写 JSON Schema、不用写参数校验、不用声明哪个参数必填。

### 函数的签名和 docstring 是唯一真相来源

`Tool.of()` 从函数本身推导出全部元数据：

| Tool 字段 | 从哪来 |
| --- | --- |
| `name` | `func.__name__` |
| `description` | docstring 第一段 |
| 每个参数的说明 | docstring 的 `Args:` 段 |
| `schema` | `inspect.signature` + `get_type_hints` |
| 哪些参数必填 | 没有默认值的那些 |

一份定义，两个消费方：

| 消费方 | 用 schema 干什么 |
| --- | --- |
| 模型 | 作为原生 tool calling 的 `parameters` 随 `tools=[...]` 发出去 |
| 参数处理 | 按 type 转换（`{"max_results": "5"}` → `5`、`{"recent": "true"}` → `True`） |

**schema 就是标准 JSON Schema，一个字段都不用改**，`openai_spec()` 只是给它
套上 `{"type": "function", "function": {...}}` 这层壳：

```json
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "计算数学表达式。支持 + - * / // % ** 和 abs/round/min/max/pow/sqrt。",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {
          "type": "string",
          "description": "数学表达式，例如 \"2 + 3 * 4\"、\"(1+2)**3\"、\"sqrt(16)\""
        }
      },
      "required": ["expression"]
    }
  }
}
```

`required` 是白送的——它直接从「参数有没有默认值」来，不用手写。

不写 `default` 是有意的：可选参数的默认值在**函数签名**里，模型省略时由 Python
生效，schema 里再写一遍就是第二个真相来源（OpenAI 的 `parameters` 也不认它）。

docstring 里的参数说明**支持跨行**：续行只要比参数名缩进更深，就会被接上。
判断缩进必须先于匹配参数名，否则像 `abs: 摘要、all: 全字段` 这种续行
会被误认成一个名叫 `abs` 的参数。

### 统一入口是 `Tool.run()`

```python
def run(self, **kwargs) -> str:
    return str(self.func(**coerce(self.schema, kwargs)))
```

循环里只有这一行，不认识任何具体工具：

```python
observation = tool.run(**json.loads(call.function.arguments or "{}"))
```

---

## ReAct 循环原理

```
                  ┌──────────────────────────────────┐
                  │  Question: 123*456-1000 = ?      │
                  └────────────────┬─────────────────┘
                                   ▼
              ┌────────────────────────────────────────┐
              │  调用 LLM（带上 tools=[...]）           │◄──────┐
              └────────────────┬───────────────────────┘       │
                               ▼                               │
              ┌────────────────────────────────────────┐       │
              │  返回 message：                         │       │
              │    .content     可以没有（或是一句话）  │       │
              │    .tool_calls  结构化调用，可以没有    │       │
              └────────────────┬───────────────────────┘       │
                               ▼                               │
                      有 tool_calls 吗？                       │
                          │            │                       │
                   没有 ──┘            └──── 有                │
                   │                          │                │
                   ▼                          ▼                │
            ┌─────────────┐      ┌──────────────────────┐      │
            │ .content 就是│      │ tool.run(**json)     │      │
            │   最终答案   │      │ （按名字查 TOOLS 表）│      │
            └─────────────┘      └──────────┬───────────┘      │
                                            ▼                  │
                                 ┌──────────────────────┐      │
                                 │ role:"tool" 消息      │──────┘
                                 │ 带 tool_call_id 回填  │  回到 LLM
                                 └──────────────────────┘
```

**核心就一句话**：把工具的返回结果作为一条 `role:"tool"` 消息追加进对话历史，
再问模型一次。模型看到自己上一步的结果，决定下一步做什么——循环往复，
直到它返回一条**不带 `tool_calls`** 的消息，那条消息的内容就是最终答案。

注意这里没有「finish 动作」了。文本模式需要模型显式说 `Action: finish`，
因为循环分不清「一段文本」和「一个调用」；而 `tool_calls` 的有无本身就是判据，
所以终止条件是内建的，不用约定。

另一个内建的好处是**一轮可以返回多个 `tool_call`**（并行调用）。`loop.py` 里
那就是个 `for`，每个 `tool_call_id` 配一条 `role:"tool"` 回填即可。

### 两个关键文件的分工

| 文件 | 负责 | 不负责 |
| --- | --- | --- |
| `loop.py` | **控制流**：调模型、执行工具、回填、刹车 | 不存状态、不碰工具细节 |
| `state.py` | **状态**：轨迹与对话历史 | 不做决策 |

`AgentState` 里同一份轨迹存了两份视图，不是冗余：

- `messages` — OpenAI chat 格式，每次请求原样发给模型
- `steps` — 结构化 `Step` 对象，给人看、给日志用、阶段二做评测用

接下来接上下文压缩时，大概率是**压缩 `messages` 但保留 `steps`**，所以分开。

---

## 子 agent

复杂的研究型问题，主 agent 先拆题再派活：

```
主 agent ──plan_research──> 子问题清单（3~5 条，带复杂度判断）
         ──delegate───────> 并行起 N 个子 agent，各自查一条
         <──四条结论───────  主 agent 自己聚合成最终答案
```

**聚合没有单独的节点**，就是 ReAct 循环里的下一轮——`delegate` 的返回值作为一条
`role:"tool"` 消息回填，主 agent 拿到它之后写最终答案。

| 工具 | 参数 | 干什么 |
| --- | --- | --- |
| `plan_research` | `topic` | 拆成互相独立的子问题，判断该不该分派 |
| `delegate` | `tasks`（数组）, `max_steps` | 并行派发，回收结论 |

四个要点，详细版见 [step3.md](step3.md)：

1. **上下文隔离** —— 子 agent 有自己的 `messages`，主 agent 只拿到最后那句结论。
   实测一次调研子 agent 侧产出 17.9 KB，进主上下文的只有四条结论。**这才是
   「省」的地方，不是省时间**（虽然并发也确实省：3 个子任务 4.8 秒）。
2. **递归防护** —— `SUBAGENT_TOOLS`（`agent/subagent.py:32`）里没有 `delegate`。
   子 agent 再派子 agent，调用量按 fan-out 的幂次涨，收益接近零。
3. **顺序保持** —— 并发收结果但按索引回填，否则会把第 3 份结论安到第 1 个标题下。
4. **成本显式封顶** —— `delegate` 超过任务数直接拒绝，错误信息告诉模型怎么收敛。

> ⚠️ **加了 delegate 之后 `LLM_MAX_TOKENS` 不能按「每步只输出三行」来定。**
> 一轮回复里可能塞着好几条任务描述，1024 会把它截断成非法 JSON，
> 报错却长得像「模型 JSON 写错了」。默认已改成 4096。

---

## 设计决策

### 1. 走过的弯路：先文本解析，再换原生 tool calling

这个项目的第一版是**文本模式**：让模型按约定输出 `Thought / Action / Action Input`
三段纯文本，再用正则解析。当时的理由是教学——手写解析器能逼你面对 ReAct 的真实样子：
模型只是吐了一段**符合约定的普通文本**，所有「工具调用」的语义都是你的代码赋予的。
看清这一点，才知道框架替你做了什么。

看清之后就该换掉了。**现在是原生 tool calling**：工具以 `tools=[...]` 交给模型，
模型返回结构化的 `tool_calls`，循环执行后按 `tool_call_id` 回填 `role:"tool"` 消息。

换过去之所以几乎没成本，是因为当初就把工具接口按「将来能平滑切过去」设计了：
`schema` 从一开始就是标准 JSON Schema，所以 **`tools/` 下三个工具文件一行都没动**，
改的只有 `loop.py` 的控制流和 `base.py` 里删掉的那层文本适配。

### 2. 文本模式踩过的坑（这就是换掉它的原因）

第一版的 `_parse` 是整个方案里最脆的地方，每发现一种模型不守格式的写法就得打一个补丁：

| 情况 | 文本模式的处理 |
| --- | --- |
| 中文冒号 `Action：` | 正则同时匹配 `:` 和 `：` |
| 反引号 / 引号 / 句号包裹 `` `calculator` `` | `.strip("`\"'*。.")` 清掉 |
| 大小写 `Calculator` | `.lower()` |
| Action Input 写成裸值 `2+2` 而非 JSON | 单参数工具自动映射到那个参数 |
| Action Input 被 ```json 包裹 | 剥掉代码围栏再 parse |
| 模型自己编 Observation | `stop=["\nObservation"]` 截断 |
| 内容在 `reasoning_content` 里 | `content` 为空时回退读取 |

这张表是**文本模式的反面教材**：每一行都是「模型没按格式来」和「我的解析器不够聪明」
之间的军备竞赛，而且永远赢不了——补丁只能覆盖见过的写法。更要命的是它治不了
「工具名写错一个字符」这种情况：`web-search` 和 `web_search` 在文本里没法区分
哪个是笔误、哪个是另一个工具。

原生 tool calling 把这些整类问题消掉了：工具名必须是 `tools=[...]` 里声明过的
（服务端会约束），参数是结构化 JSON，不需要 `stop` 截断，也不需要正则。

代价是**它把这个过程藏进了 API**，你看不到「模型其实只是选了个名字」这件事了。
所以两种写法都值得写一遍——第一版的价值不在代码，在于让你知道框架替你做了什么。

> 唯一从文本模式**继承下来**的兜底是 `reasoning_content`：有的推理模型把内容放在
> 这个字段里而 `content` 是空的。它在 `loop.py:_final_text()` 里，仍然需要。

### 3. 错误不抛出，而是变成 tool 消息

```python
try:
    return tool.run(**json.loads(call.function.arguments or "{}"))
except Exception as exc:
    return f"错误：{type(exc).__name__}: {exc}"
```

工具报错 / 工具名不存在 / 参数不是 JSON / 参数不是对象——**一律不中断循环**，
而是把错误信息当作 `role:"tool"` 消息回填给模型。模型看到 `ZeroDivisionError`
会自己换个算法。

这是 ReAct 比「一次性调工具」强的地方：**它容错，而且错得越多自我修正的机会越多**。
唯一需要人兜底的是 `max_steps`（默认 8），防止模型绕圈子烧钱。

实测有效：`web_search` 没配 key 时会抛「缺少 TAVILY_API_KEY」，模型收到这条
tool 消息之后没有卡死，而是自己收场并说明搜索不可用。

### 4. 计算器为什么不用 `eval()`

`eval("__import__('os').system('rm -rf /')")` 是会真的执行的。
`tools/calculator.py` 改用 `ast` 白名单遍历：只放行算术运算节点和
`abs/round/min/max/pow/sqrt` 六个函数，其他一律 `ValueError`。
模型给的东西是**不可信输入**，这条线从第一天就要划。

### 5. 为什么让函数签名当 schema

因为**手写元数据会漂移**。改造前是这样：

```python
def calculator(expression: str) -> str:          # ← 签名在这里

params={"expression": '数学表达式字符串，例如 "2 + 3 * 4"'},   # ← 这里又描述一遍
```

同一个参数写两处，改了签名忘改描述就是错的（而且不会报错，只会让模型看到过时信息）。
推导之后只剩签名一处。

代价也要认：**改 docstring 不等于改参数**。实测中删某参数时只删了 docstring 的说明行、
没删签名，结果发出去的 schema 里出现了一个「没有描述的可选参数」——因为 schema 是从签名推的。
所以这个设计下，签名是契约，改它要当回事。

### 6. 哪些参数不该暴露给模型

`web_search` 现在**只有一个参数 `query`**。另外两个旋钮都写死在代码里了：

| 旋钮 | 值 | 位置 |
| --- | --- | --- |
| `search_depth` | `basic`（1 额度） | `web_search.py` 的 payload |
| `max_results` | `3` | `web_search.py:_MAX_RESULTS` |

`max_results` 一开始是暴露的（`max_results: int = 3`）。问题在于**默认值不等于
强制值**：模型省略它才是 3，一旦它自己传个 8 或 10，返回的就是 8 或 10 条。
实测里模型经常自己传值。而模型没有任何判断该要几条的依据——它看不到额度，
看不到延迟——描述怎么写都会诱导它往多了要。所以干脆从签名里删掉，写死。

`search_depth` 是同一个故事，而且更早暴露出来：Tavily 分 `basic`（1 额度）和
`advanced`（2 额度）两档，一开始我把 `deep: bool` 给了模型，实测**模型对
「Python 3.13 有什么新特性」这种普通问题也开了 advanced**。更糟的是 advanced
每条内容长 5–20 倍，而工具只截取开头 400 字——等于花双倍钱把它买来的东西
又扔掉大半。所以也写死成 `basic`。

判断标准是**模型有没有能力做这个决定**：

- 是**意图**问题吗？（用户说「最新的」还是「最经典的」）→ 暴露，模型能从问题里读出来
- 是**成本 / 预算**问题吗？→ 写死，模型无从判断

按这个标准，`arxiv_search` 的 `recent` 是暴露的（意图问题），而 `arxiv_search`
的 `max_results` 目前还留着——严格说它和 `web_search` 的那个是同一类问题，
只是还没动手砍。

少一个参数的好处是三重的：schema 更短、模型填错的机会更少、你对自己的花费
有确定的预期。

### 7. 为什么 `arxiv_search` 不用官方 `arxiv` 包

曾经参考过一个用 `arxiv` PyPI 包的项目（它更省事：自带 3 秒限速、重试、解析）。
最后没用，因为：

1. **它解决不了真正的问题**。实测在同一台机器上，`arxiv` 4.0.1 拿到的是和我自己写的
   urllib 版本**一样的 406**——那是 arXiv 按 IP 限流，不是 HTTP 库或 URL 的问题。
2. **依赖代价**。`arxiv` 依赖 `requests` + `lxml`，而本项目的卖点之一是只有两个依赖
   （`openai` + `python-dotenv`）。为一个搜论文的工具引入一个 C 扩展，不值。

它有价值的地方我已经抄过来了：**请求前强制 3 秒间隔**（`_MIN_INTERVAL_SECONDS`），
这是防限流的关键——撞墙之后再退避是被动的。

### 8. 为什么 `arxiv_search` 要自带重试，还要熔断

arXiv 的限流手段很硬：要求「每 3 秒最多一个请求」，超了返回 406，而且**封禁是粘性的**
——一旦触发，之后连完全正常的请求也一起被挡，能持续好几分钟。

实测同一个 URL 连续打，会得到 `200 / 406 / 406 / 200` 这种自相矛盾的结果。
所以「换个 URL 或换个 HTTP 库就能绕过」是错觉。

分三层应对：

| 层 | 手段 | 常量 |
| --- | --- | --- |
| 防 | 请求前强制间隔，从源头不踩线 | `_MIN_INTERVAL_SECONDS = 3.0` |
| 救 | 撞上 406 / 503 后退避 5s、15s 再试 | `_RETRY_WAITS` |
| 断 | 退避全败 → 锁 180 秒，窗口内直接失败 | `_BAN_SECONDS` |

退避只给偶发抖动一个机会——实测单次 406 确实有抖动成分（上表那个 `200/406/406/200`），
值得再试一下。

**真封住的时候靠的是熔断。** 封禁以**分钟**计，5/15 秒跨不过去；更要命的是每一次
尝试都在刷新封禁，越试封得越久。所以退避全败后记一个到期时间，窗口内不再发请求、
立刻失败，错误信息里直接建议模型改走 `web_search`。

代价是**熔断状态是进程内的**：换一个进程、或者等窗口过期，就重新开始试。

---

## 验证状态

诚实地说清楚哪些跑过、哪些没跑过。

### ✅ 真实跑过

- **`calculator`** —— 全程真实可用，多轮计算正确
- **`web_search`** —— 真实调用 Tavily API 成功，返回真实中文结果，
  模型能正确选择它并带上合适的参数
- **原生 tool calling 全链路** —— `deepseek-v4-flash` 支持 OpenAI 兼容的
  `tools=[...]`。真实跑通：模型发 `tool_calls` → 循环执行 → 回填 `role:"tool"`
  → 下一轮模型看到结果 → 最终直接内容作答收尾。多轮（web_search 后接
  calculator）也验证过
- **统一接口本身** —— 元数据推导、类型转换（`"5"` → `5`、`"true"` → `True`）、
  `openai_spec()` 生成的 JSON、docstring 参数说明跨行续接
  （这个最初是错的：只取第一行、续行全丢，导致模型看到半句话的参数说明，已修）
- **错误路径** —— 缺 key、401、未知工具、工具抛异常、参数不是合法 JSON、
  参数不是对象、`max_steps` 刹车：全部走「变成 tool 消息让模型自愈」而不是中断
  （用假 LLM 客户端逐条构造验证过，见「没有的东西」）
- **并行 tool_call** —— 一轮返回两个 `tool_call` 时逐个执行、逐个按
  `tool_call_id` 回填。用假客户端验证过；真实模型这次没主动并行调用过
- **多工具选择** —— `calculator` / `web_search` / `arxiv_search` 同时在表里时，
  模型选对了
- **超时与重试** —— LLM 客户端显式配了 `Timeout(connect=10, read=600, write=30,
  pool=10)` 和 `max_retries=2`；arxiv 熔断、web_search 的 5xx 重试用**打桩的假连接**
  逐条验过：`500 → 200` 重试成功、`429` / `401` 一次即抛不重试、
  熔断窗口内 0.0000 秒失败且不产生新请求、退避全败后锁住 180 秒

### ⚠️ 没有验证

- **`arxiv_search` 的成功路径**。XML 结构和字段路径是看着真实响应写的
  （和参考项目的解析逻辑也对得上），但**解析 + 格式化的代码一次都没跑成功过**
  ——每次都被 arXiv 的 IP 限流挡在门外。`recent=True` 的排序同样没跑过。
- **`_ABSTRACT_LIMIT = 900` 这个值**。拍脑袋定的，没量过真实摘要长度
  （对比：`web_search` 的 400 是实测量出来的——实测 5 条全部触发截断，
  而 Tavily 文档说 basic 是 200–300 字，与实测不符）。
- **`web_search` 的 `_SNIPPET_LIMIT` 之外还有多少省略**。
- **真实模型主动并行调用工具**。代码支持，路径用假客户端验证过，
  但实测里模型每次都规规矩矩一轮调一个。

### ❌ 没有的东西

- **没有任何测试文件**。项目里 `find . -name "*.py"` 只有 9 个源文件。
  上面那些验证是开发过程中临时写脚本跑的，跑完删了，**不可复现**。
  这是当前最大的缺口——原生 tool calling 那次改造就是**在没有回归保护的情况下**
  动 `loop.py` 和 `tools/base.py` 的，靠的是临时脚本加真实端到端跑一遍。
  能过是因为改动面小、且假客户端脚本覆盖了七条降级路径；下次未必这么运气。

---

## 已知限制

- **上下文只增不减**。`messages` 只追加不清理，实测一次 `web_search`
  就往里塞 **2665 字**，跑完一个两步任务累计 4551 字。
  （早先这里写的是「无界增长」——**不准确**：一次 `run_agent` 的消息数被
  `max_steps=8` 锁死在 42 条以内，是有界的。真正会无界增长的是跨轮对话历史，
  而那个还没做。）
- **两层上下文都要管，现在只做了上面一层**。子 agent 解决了「工具结果进主上下文」，
  但它**自己的** `messages` 照样会涨——跑 5 步、每步 2665 字，一样会满。
  以前只有一层要管，现在有两层，而第二层还没做任何压缩。
- **子 agent 失败就是失败**，没有重试。一个子 agent 撞上步数上限或网络错误，
  它的子任务就没有结论，只能靠主 agent 如实说明「这块没查到」。
- **`delegate` 的并发没有压测过**。`print_lock` 只在小规模（4 个）下跑过，
  任务数拉满到 8 时的终端输出交错、以及 Tavily/arxiv 的限流叠加，都没有验证。
- **`arxiv_search` 的限流无法绕过**。同一 IP 短时间内连查多次会被粘性封禁几分钟。
  熔断能让它**不再白等**（窗口内立刻失败，并建议模型改走 `web_search`），
  但绕不过去——只能等。而且熔断状态只在进程内，重启就忘了。
  **子 agent 并发会放大这个问题**：四个子 agent 同时打 arxiv，比串行更容易触发封禁。
- **没有持久化**，进程退出状态就没了。
- **主循环里的工具是串行执行的**。一轮里的多个 `tool_call` 是按顺序 `for` 循环
  跑的，没有并发——所以 `delegate` 的并发做在了**工具内部**，而不是靠一轮发
  多个 tool_call（那样只是排队）。
- **`max_steps` 数的是模型调用轮次**，不是工具调用次数。一轮里并行 N 个工具
  只算一步——理论上一次可以在一步内打满所有工具。
- **`.env` 只在走 `main.py` 时被加载**，单独用 `tools/` 要自己 `load_dotenv()`。
  子 agent 让这个坑更要紧了：`tools/*.py` 里的工具现在会在函数体内读一堆环境变量。

---

## 下一步

按优先级：

1. **补测试**。这件事已经欠了三轮改造了。子 agent 那一批 bug（见 [step3.md](step3.md)）
   **大半是跑真实任务才暴露的，单元测试一个都没发现**——并发逻辑、提示词
   约束、token 上限这三类问题尤其需要固定用例。用假 LLM 客户端就能覆盖大半，
   不需要任何 key。
2. **评估子 agent 的实际研究质量**。跑一批真实题目，看 `plan_research` 拆得好不好、
   `delegate` 派得对不对、聚合有没有丢信息。现在只有一个成功样本。
3. **修 `.env` 加载的坑**。抽个公共 `config.py` 让 `agent/` 和 `tools/` 都导入，
   或者把 `load_dotenv` 也放进 `tools/__init__.py`（3 行，幂等）。
   注意子 agent 让这个坑更要紧了：`tools/*.py` 里的工具现在会在函数体内读
   一堆环境变量。
4. **子 agent 的失败重试**。现在一个子 agent 失败就是失败，没有「换个查询词再来」
   的机制，也没有把失败子任务重新派出去的逻辑。
5. **上下文压缩**。优先级比原来低了——子 agent 已经替掉了收益最大的那块。
   还剩两处：主 agent 自己查的那几轮，以及子 agent 自己的上下文。
   要不要做，取决于之后接不接跨轮 history。
6. **验证 `arxiv_search`**。等限流窗口过去，跑一次真实查询，
   顺便量出真实的摘要长度来定 `_ABSTRACT_LIMIT`。

再往后是阶段四：规划与反思。
#