# Research Agent 分层架构

本文记录项目当前的分层设计：

- **外层**使用 LangGraph 编排完整研究流程
- **内层**由各个 Agent 使用 ReAct 循环完成具体任务

## 1. 两个概念

### ReAct

ReAct 是 Agent 的执行模式：

```text
调用模型
  ↓
决定是否使用工具
  ↓
执行工具
  ↓
观察工具结果
  ↓
再次调用模型
```

当前项目的 `agent/loop.py` 已经手动实现了这个循环。

### LangGraph

LangGraph 是流程编排框架，用图表示：

- 状态
- 节点
- 节点之间的边
- 条件路由
- 循环
- checkpoint

LangGraph 不等于 ReAct，但可以用来编排 ReAct 循环，也可以编排更复杂的研究工作流。

## 2. 外层和内层

推荐的整体结构是：

```text
外层：研究流程图
┌─────────┐
│ Planner │
└────┬────┘
     ↓
┌───────────┐
│ Researcher│
└─────┬─────┘
      ↓
┌────────┐
│ Critic │──── 继续研究 / 重新规划 / 停止
└────┬───┘
     ↓
┌────────┐
│ Writer │
└────┬───┘
     ↓
┌───────────┐
│ Validator │
└───────────┘

内层：每个 Agent 的任务循环
调用模型 → 调用工具 → 读取结果 → 再调用模型 → 得出结论
```

外层决定“研究流程走到哪一步”，内层决定“当前节点如何完成自己的任务”。

## 3. 外层流程

外层 LangGraph 当前定义为：

```text
START
  ↓
plan
  ↓
research
  ↓
critic
  ├─ revise   → revise → research
  └─ stop     → write
                         ↓
                      validate
                         ↓
                       END
```

各节点职责：

| 节点 | 职责 |
| --- | --- |
| `plan` | 将研究主题拆成子问题和查询 |
| `research` | 收集网页、论文、文件或其他来源 |
| `critic` | 判断已有证据是否足够，并选择补查或写作 |
| `revise` | 把 Critic 提出的补充查询加入待查队列 |
| `write` | 根据研究发现生成报告 |
| `validate` | 检查引用、来源和关键论断 |

其中 `critic` 不应完全依赖模型决定流程。代码应先检查最大步数、token 预算和待处理任务，再使用模型进行语义判断。

## 4. 内层 ReAct

每个需要自主完成任务的节点，都可以使用独立的 ReAct 循环：

```text
┌──────────┐
│ LLM Node │
└────┬─────┘
     ↓
有 tool_calls？
  ├─ 是 → Tool Node → 回到 LLM Node
  └─ 否 → 返回节点结果
```

例如，一个 Researcher 子 Agent 可以：

1. 根据子问题生成搜索查询
2. 调用网页搜索或 arXiv
3. 阅读工具结果
4. 决定是否换一个查询词
5. 整理出带来源的研究结论

它只负责完成自己的子任务，不负责决定整个研究流程是否结束。

## 5. 与当前代码的对应关系

| 代码 | 职责 |
| --- | --- |
| `agent/loop.py` | 内层手写 ReAct 执行引擎 |
| `agent/state.py` | 内层状态和 `ResearchFinding` |
| `agent/langgraph/lg_states.py` | 外层研究状态 |
| `agent/langgraph/lg_prompts.py` | 外层 Critic 提示词 |
| `agent/langgraph/lg_nodes.py` | 默认研究、写作、校验等实现 |
| `agent/langgraph/lg_builder.py` | 模块级节点、路由和图连线 |
| `agent/langgraph/main.py` | 外层研究命令行与报告保存 |
| `tools.run_tool()` | 内层工具执行入口 |
| `plan_research` | 默认 Planner 实现 |
| `run_subagents()` | 外层 Researcher 默认批量实现 |
| `agent/subagent.py` | 子 Agent 手写 ReAct 循环 |

`lg_builder.py` 文件末尾集中调用 `StateGraph.add_node()`、`add_edge()` 和 `add_conditional_edges()`。节点是模块级函数，通过 `RunnableConfig.configurable.research_services` 获取本次运行的可替换实现。`ResearchGraph` 提供运行和依赖注入接口；`main_graph.py`、`agent/graph.py` 和 `agent/research_state.py` 保留旧入口。

当前模型客户端和工具调用都是同步接口，外层使用 `graph.invoke()`，手写 Agent 的 `delegate` 用线程池并行。启用 checkpoint 后，外层 research 按查询逐个完成并保存进度。因此节点使用普通 `def`；改用 `async def` 需要同时改造底层 I/O 和运行入口。

## 6. 推荐的状态划分

外层状态保存研究流程信息：

```python
class ResearchState:
    topic: str
    subquestions: list
    findings: list
    frontier: list
    depth: int
    token_used: int
    critic_signal: str
    report: str
    citations: list
```

内层 Agent 状态保存一次任务的执行信息：

```python
class AgentState:
    task: str
    messages: list
    steps: list
    final_answer: str | None
```

两层状态需要隔离：

- 子 Agent 的原始搜索结果不直接进入主 Agent 的消息历史
- 主 Agent 只接收子 Agent 的结构化结论
- 外层状态保存研究发现和来源
- 内层状态保存当前任务的 ReAct 轨迹

## 7. 上下文管理位置

上下文压缩也分两层：

### 内层压缩

控制单个 Agent 的消息历史：

- 限制最大轮数
- 限制工具结果长度
- 压缩较早的搜索结果
- 保留最近工具调用和最终结论

### 外层压缩

控制研究发现：

- 按来源去重
- 按子问题归类
- 合并同一来源的多条发现
- 保留来源、引用编号和置信度
- 写报告前再压缩一次

外层应优先保存结构化的 `ResearchFinding`，而不是长期保存完整的原始 `messages`。

## 8. 实施进度

已实现：

- 结构化 findings、去重和上下文压缩。
- 手写 ReAct 内层和 LangGraph 外层研究图。
- Critic 条件路由、Validator 和研究预算。
- 外层图按查询保存 checkpoint；`ResearchGraph` 注入内存或 PostgreSQL saver，支持按 `thread_id` 查看和恢复。

PostgreSQL 跨连接恢复已验证；独立进程退出后的恢复边界和运行轨迹仍待验证或实现。固定主题评测已有 [流程计划](../eval/README.md)，题集、脚本和基线结果尚未实现。之后再扩展 RAG 和长期记忆。

## 9. 设计原则

- LangGraph 负责流程，不负责替代工具和研究逻辑
- ReAct 负责单个 Agent 的任务执行
- 外层用条件边控制研究是否继续
- 内层用工具调用完成具体检索
- 研究证据和聊天消息分开保存
- 模型负责提出判断，代码负责执行预算和路由

