# 阶段三：子 agent（plan_research / delegate）

阶段二后半的「上下文管理」还没做，先做了子 agent——因为**子 agent 本身就是最有效的
上下文管理手段**：它让工具结果的原文根本不进主上下文。

| # | 原始待办 | 状态 | 落在哪 |
| --| --- | --- | --- |
| 1 | 上下文压缩 | ❌ | 见第七节，先被本阶段替代了一部分 |
| 2 | 子 agent | ✅ | `agent/subagent.py`、`tools/plan.py`、`tools/delegate.py` |
| 3 | 主循环拆分 | ✅ | LLM 层 → `agent/llm.py`；工具执行 → `tools/__init__.py:61` |
| 4 | 提示词集中 | ✅ | `prompts.py`（项目根目录，见第四节末尾） |

---

## 一、这不是「多个 agent 同时搜」，是「一个 agent 派活」

两个工具，由主 agent 自己在 ReAct 循环里决定什么时候用：

| 工具 | 干什么 | 位置 |
| --- | --- | --- |
| `plan_research` | 把主题拆成互相独立的子问题，判断该不该分派 | `tools/plan.py:131` |
| `delegate` | 把子任务并行派给子 agent，回收它们的结论 | `tools/delegate.py:51` |

主 agent 的流程是：**复杂问题 → plan_research 拆题 → delegate 并行派发 → 自己聚合**。

聚合**没有单独的节点**：`delegate` 的返回值作为一条 `role:"tool"` 消息回填，循环的
下一轮就是聚合。刻意不额外调一次模型做「总结」——再插一次调用只是多花钱、多丢一层
信息。

## 二、三个关键设计

### 1. 上下文隔离（最重要的一条）

子 agent 有自己的 `messages`（`subagent.py:67`），主 agent 只拿得到它的**最后一句
结论**，中间工具结果一概看不到。

实测：一次调研跑完，子 agent 侧产生 **17.9 KB** 日志，进主上下文的只有四条结论，
还被 `format_digest` 按 `_ANSWER_LIMIT`（`subagent.py:36`）硬截到每条 2000 字。
**省上下文靠的是这里，不是省时间。**

### 2. 递归防护

`SUBAGENT_TOOLS`（`subagent.py:32`）里**没有 `delegate`，也没有 `plan_research`**：

```python
SUBAGENT_TOOLS = ("web_search", "arxiv_search", "read_file", "calculator")
```

子 agent 再派子 agent，调用量按 fan-out 的幂次涨，收益接近零（子任务已经是拆好的）。
`to_openai_tools(only=...)`（`tools/__init__.py:49`）就是为这个过滤加的。

### 3. 顺序保持

`run_subagents`（`subagent.py:107`）用 `as_completed` 收结果，但**按索引回填**，
不用 append：

```python
futures = {pool.submit(...): i for i, task in enumerate(tasks)}
...
results[index] = result
```

不这么做，聚合时会把第 3 份结论安到第 1 个标题下。日志里完成顺序是 `2 → 1 → 3 → 4`，
输出顺序仍是 `1 → 2 → 3 → 4`。

## 三、为什么 delegate 是「批量」而不是「单个」

单任务版 `delegate(task)` 看着更简单，但**并行不起来**：主循环串行执行 `tool_calls`
（`loop.py:136` 那个 `for`），一轮里发四个 tool_call 也是排队跑。

所以并发只能做在工具**内部**——代价是工具得知道「怎么并发」，这才有了 `run_subagents`。

实测：3 个子任务 6 次模型调用，**4.8 秒**跑完（串行约 10 秒以上）。

### 并发是怎么实现的，以及它的边界

用**线程池**，不是 async：

```python
futures = {pool.submit(run_subagent, task, ...): i for i, task in enumerate(tasks)}
for future in as_completed(futures):
    results[futures[future]] = result
```

`as_completed` 让先跑完的先被处理，但结果按**索引**回填，所以对外顺序永远是
`1→2→3→4`（见上节）。打印加 `print_lock`，否则两行字符会交错。

没用 async 不算缺陷：async 和线程解决同一个问题，不提供新能力。整条调用链是同步的
（`main.py` → `run_agent` → `run_tool` → `delegate`），改 async 得一路改到工具签名、
还得给 `run_tool` 分同步/异步两套；而瓶颈是网络等待不是 CPU，线程卡在 socket read 时
会释放 GIL，是真并行。

但线程方案有三个边界：

1. **没有整批超时。** `as_completed` 和 `with` 退出都要等所有 future，**最慢的那个
   决定整批耗时**。而单步的 read timeout（`LLM_READ_TIMEOUT=600`）算的是「两个 token
   之间的间隔」，不是总时长（`llm.py:77-79`）。
2. **Ctrl-C 不灵。** 线程池退出走 `shutdown(wait=True)`，而 Python 杀不掉正在跑的
   线程——跑长调研时按 Ctrl-C，得等子 agent 全跑完才响应。
3. **只有 I/O 密集才真并行。** GIL 只在等网络时释放，以后加 CPU 密集的工具会把其他
   线程一起卡住。这条 async 也躲不掉（更糟，会阻塞整个事件循环）。

第 1 条最值得修。

#### 为什么不是 async：拿三个真 bug 试过

上面那句「async 不提供新能力」不是想当然，是拿三个真实的并发 bug 逐条试出来的。
三个都出在工具层，而且**都是 delegate 引入的**——代码写的时候都对，加了并发才变错。

| bug | 根因 | async 能修吗 |
| --- | --- | --- |
| `calculator`：`9**9**9` 冻住进程 | 计算结果没有上界 | **不能**。单事件循环只会更死，那段计算里也没有 `await` 点可打断 |
| `web_search`：模块级连接被多线程共用 | 共享可变状态不是线程安全的 | **表面能**，但那等于用「没有并发」换「没有竞态」 |
| `arxiv_search`：限速器先读后写 | 临界区没有互斥 | **不能**。改成 `await asyncio.sleep` 反而把竞态暴露在挂起点上 |

- **`calculator`** 是 CPU 密集：大整数乘法期间 CPython 不释放 GIL，而 asyncio 只有
  **一个**事件循环线程，进去就彻底死。想超时得把计算扔进独立**进程**再杀掉
  （`ProcessPoolExecutor`），比线程重一个量级。真正的修法是给结果设上界——那是参数
  校验，跟并发模型无关。
- **`web_search`** 的 `http.client` 是纯同步的：`request()` 和 `getresponse()` 之间
  没有 `await`，事件循环没法在中间切走。所以竞态**确实会消失**——代价是四个子 agent
  严格排队，各自独占事件循环一个完整网络往返。
- **`arxiv_search`** 照搬同步写法就同上退化成串行；老实改成 `await asyncio.sleep`
  则竞态**原样回来**，临界区里还多了一个真正的挂起点。这是 asyncio 最经典的误解：
  **没有线程 ≠ 没有竞态**——它在每个 `await` 处切任务，和线程在任意指令处切本质相同，
  只是切点更明确。

最要命的是这条：如果为了保住并发，用 `await asyncio.to_thread(web_search, ...)` 把
同步工具包起来——`asyncio.to_thread` 底下**就是一个 ThreadPoolExecutor**（CPython 的
默认执行器）。绕一圈回到线程，模块级全局照样被多个线程共享，bug 原封不动。

**async 在这个项目里既修不了这三个 bug，也带不来现在没有的好处。** 真正解决它们的是
三件和并发模型无关的事：给结果设上界、把共享状态改成每线程一份、给临界区上锁——写法
都在代码里（`tools/calculator.py`、`tools/web_search.py`、`tools/arxiv_search.py`），
各自的注释里写了当时的取舍。


## 四、代码拆分：loop.py 只剩控制流

加子 agent 逼出了这次拆分：子 agent 也要调模型、也要执行工具，这两段逻辑不能各写两份。

| 原来在哪 | 现在在哪 | 为什么 |
| --- | --- | --- |
| `loop.py` 的 `_call_llm` / `_make_client` | `agent/llm.py:109` / `:59` | 两个消费方（主循环、子 agent） |
| `loop.py` 的 `_run_tool` | `tools/__init__.py:61` `run_tool()` | 同上 |
| `loop.py` 的流式打印 | 留在 `loop.py:59` | 这是展示层的事，子 agent 不打印 |

**客户端改成了进程内单例**（`llm.py:93` `get_client()`）。OpenAI SDK 底层是 httpx
连接池、本身线程安全，四个子 agent 共用一份比各建一份省四次 TLS 握手。


### 提示词住在哪：三层，一层一个理由

想改模型的行为，得先知道话写在哪。这个项目里散在三层：

| 层 | 在哪 | 为什么在这 |
| --- | --- | --- |
| system 提示词 | `prompts.py`（**项目根目录**） | 整段不变、直接当身份设定发给模型 |
| 工具 docstring | 各自的 `tools/*.py` | 被 `tools/base.py` 自动推导成 description 和参数说明，是函数签名的一部分 |
| 工具返回的指令 | `plan.py` 的 `_render`、`subagent.py` 的 `format_digest`、各处错误信息 | 按运行时情况拼出来（判定复杂还是简单、哪几个子任务失败），和分支逻辑长在一起才读得懂 |

只有第一层搬进了 `prompts.py`。第二层搬走等于元数据散成两处，正是 `tools/base.py`
模块 docstring 要避免的；第三层搬走会和分支逻辑分家。

**`prompts.py` 为什么在根目录**：`tools/plan.py` 要在模块级 import 它，而
`import agent.prompts` 会先执行 `agent/__init__.py`，那里 `from .loop import run_agent`、
loop 又 `from tools import ...`——正好绕回同一个环。放在根目录、且**不 import 任何
本项目模块**，它就是个安全的叶子节点。

## 五、成本

这是用这个功能唯一要盯的东西。总模型调用量 ≈ **子任务数 × 每个的步数**。

| 旋钮 | 默认 | 位置 |
| --- | --- | --- |
| `SUBAGENT_MAX_TASKS` | 8 | `.env` |
| `SUBAGENT_MAX_WORKERS` | 4 | `.env` |
| `SUBAGENT_MAX_STEPS` | 5 | `.env` |
| `PLAN_MAX_SUBQUESTIONS` | 5 | `.env` |
| `LLM_MAX_TOKENS` | 4096 | `.env` |

上限不是「建议」：`delegate` 超过任务数**直接拒绝**（`delegate.py:22`），错误信息
里会写清楚「请合并相近的条目，或只派最重要的 N 个」，让模型自己收敛。


## 六、和「上下文压缩」的关系

1.md 那套四步压缩管线，前三步（`tool_result_budget` / `snip_compact` /
`micro_compact`）**都是为「工具结果在上下文里堆积」服务的**。子 agent 把这个前提改掉了
一部分——**它的工具结果根本不进主上下文**——所以本阶段替代了压缩机制里收益最大的那块。

真正还需要压缩的场景只剩两个：

1. **主 agent 自己查的那几轮**（简单问题不走 delegate，工具结果照样堆积）
2. **子 agent 自己的上下文**（跑 5 步，`web_search` 一次 2665 字，也会满）

第 2 条是新出现的：以前只有一层上下文要管，现在有两层。

## 七、下一步

**阶段四（step4）的主题定了：上下文与记忆。**

顺序不能反：现在做压缩没有意义——主上下文被 `max_steps=8` 和 delegate 一起封住，
1.md 那套阈值永远够不着，等于给一个不会满的容器装泄压阀。**先有跨轮记忆，上下文才会
真的开始长，压缩才有对象。**

阶段四之前，阶段三还欠三件事：

1. **给每个子 agent 加总时长上限**（见第三节末尾）。现在最慢的那个决定整批耗时，线程
   挂死没有任何兜底——修掉的那三个并发 bug 只堵住了最容易被触发的路，任何一次没预料到
   的挂死（比如一个死循环）照样会拖住整批。这条不需要在「线程」和「async」之间做选择。
   （传输层重试不用单做：已经放进 `call_llm`，三个调用方自动都有。）
2. **补测试**。这一阶段的 bug 大半是跑真实任务才发现的，测试欠账已经很大了。子 agent
   是纯并发逻辑，用假 client 就能覆盖大半——`web_search` / `arxiv_search` 两组 A/B、
   `call_llm` 的 9 个用例、`plan_research` 的内容层重试都是现成模板（换掉 `urlopen` /
   `_get_connection` / `_call_once`，同一份负载跑两遍，看行为差异）。
3. **评估实际研究质量**。跑一批真实题目，看 `plan_research` 拆得好不好、delegate 派得
   对不对、聚合有没有丢信息。端到端复跑已经露出一个样本：5 个子任务里 2 个跑满步数
   失败（见第六节）。步数上限现在能配到 10，但该不该往上调还没有实测依据——这正好是
   这批题目要回答的问题之一。

阶段四按这个顺序做：

1. **跨轮对话记忆**。先做最简单的追加式：把 `main.py` 交互循环里的 `messages` 从
   「每轮新建」改成挂在循环外。几行就能验证「记得住上一轮」到底有没有用。
2. **观察它多快长满** → 到那时才上 1.md 的压缩管线（`snip_compact` / `compact_history`）。
3. **子 agent 自己的上下文压缩**。两层上下文里只有这一层完全没有兜底——跑 5 步、
   `web_search` 一次 2665 字，它自己也会满。

### 顺带记一个没修的：`steps` 是死代码

`state.py` 的 `AgentState.steps` 和 `finished` 目前没有任何调用方——`main.py` 拿到
`state` 只用了流式输出，`trace()` 也没人调。它们是为「给人看」和「阶段四做评测」留的，
先留着，但要知道现在是死的。
