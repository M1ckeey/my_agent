# Research Agent 分层架构

本文记录项目后续采用的分层设计：

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

外层 LangGraph 可以定义为：

```text
START
  ↓
plan
  ↓
research
  ↓
critic
  ├─ continue → research
  ├─ revise   → plan 或 research
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
| `critic` | 判断证据是否足够、是否需要继续 |
| `revise` | 修改查询或重新规划 |
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

| 当前代码 | 后续图结构 |
| --- | --- |
| `agent/loop.py` | 内层 ReAct 图 |
| `agent/state.py` | 外层和内层的状态定义 |
| `tools.run_tool()` | Tool 节点 |
| `plan_research` | Planner 节点 |
| `delegate` | Researcher 批量节点或子图 |
| `agent/subagent.py` | 子 Agent ReAct 子图 |
| `messages` | 当前 Agent 的消息上下文 |
| `steps` | 执行轨迹和调试信息 |

初期可以让 `delegate` 继续作为普通工具，由工具内部的线程池并行运行子 Agent。后续再把每个子 Agent 改成 LangGraph 子图。

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

## 8. 迁移顺序

建议按以下顺序实现：

1. 为工具结果增加结构化的 finding 表示
2. 把当前 `loop.py` 拆成 LLM 节点、Tool 节点和条件路由
3. 用 LangGraph 重写主 Agent 的 ReAct 循环
4. 保留 `delegate` 的线程池实现，先接入外层研究流程
5. 增加 `plan → research → critic → write` 外层图
6. 加入 findings 去重和上下文压缩
7. 将子 Agent 改成独立的 ReAct 子图
8. 最后再增加 checkpoint、RAG 和长期记忆

## 9. 设计原则

- LangGraph 负责流程，不负责替代工具和研究逻辑
- ReAct 负责单个 Agent 的任务执行
- 外层用条件边控制研究是否继续
- 内层用工具调用完成具体检索
- 研究证据和聊天消息分开保存
- 模型负责提出判断，代码负责执行预算和路由

