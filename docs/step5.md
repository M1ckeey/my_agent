# Step 5：外层 LangGraph 编排

Step 5 引入了研究流程图。本文按当前代码说明图的组织方式；后续加入的预算、重试和校验能力见 [step7.md](step7.md)，checkpoint 与中断恢复见 [step8.md](step8.md)。内层 Agent 仍由手写 ReAct 循环执行具体工具调用。

## 流程

```text
START → plan → research → critic ─┬─ revise → revise → research
                                  └─ stop → write → validate → END
```

`plan` 把主题拆成子问题，`research` 逐个处理 `frontier` 中的查询并保存进度，`critic` 根据已有 findings 判断是否补查或结束，`write` 整理 findings，`validate` 检查子问题覆盖和报告引用。手写 Agent 的 `delegate` 仍使用线程池并行运行子 Agent；外层图为保证逐查询恢复而顺序执行查询。

## 文件组织

| 文件 | 职责 |
| --- | --- |
| `agent/langgraph/lg_states.py` | 定义 `ResearchState` 和 `ResearchSubQuestion` |
| `agent/langgraph/lg_prompts.py` | 保存 Critic 的系统提示词 |
| `agent/langgraph/lg_nodes.py` | 提供规划结果解析、默认研究器、Critic、Writer 和 Validator 等实现 |
| `agent/langgraph/lg_builder.py` | 定义模块级图节点、条件路由以及 `StateGraph` 的节点和边 |
| `agent/langgraph/main.py` | 运行研究流程并保存报告 |

项目根目录的 `main_graph.py` 是命令行兼容入口；`agent/graph.py` 和 `agent/research_state.py` 保留旧导入路径。

## 图节点与连线

`lg_builder.py` 中的 `plan`、`research`、`critic`、`revise`、`write`、`validate` 是模块级函数。节点读取 `ResearchState`，返回需要更新的状态字段。`route_research` 在还有待查查询且预算允许时继续研究。`route_critic` 读取 `revise` 或 `stop` 信号；只有补充查询尚未完成或排队、且预算允许时，才进入 `revise`。

文件末尾集中定义 `builder = StateGraph(ResearchState)`，随后调用 `add_node()`、`add_edge()`、`add_conditional_edges()`。模块级兼容导出 `graph` 使用内存 checkpointer；实际运行由 `ResearchGraph` 使用传入的 checkpointer 编译图。

`ResearchGraph` 提供 `run()`、`get_state()`、`resume()` 和测试中的 Planner、Researcher、Writer 等实现注入。它把这些实现放进本次调用的 `RunnableConfig.configurable.research_services`；各节点从配置中读取。服务对象不进入 checkpoint，恢复时重新注入。

```python
from agent.langgraph.lg_builder import ResearchGraph
from agent.langgraph.lg_states import ResearchSubQuestion

runner = ResearchGraph(
    planner=lambda topic: [ResearchSubQuestion(id="q1", question=topic)],
    researcher=lambda tasks: [],
    verbose=False,
)
state = runner.run("研究主题")
```

## 状态与预算

外层 `ResearchState` 保存主题、子问题、`frontier`、已完成查询、findings、研究深度、token 用量、报告和校验结果。它不保存子 Agent 的完整消息历史。`research` 每次处理一个查询，并把 findings 关联到子问题；`write` 在生成报告前压缩 findings，同时保留 `raw_findings`。

预算由 `RESEARCH_MAX_DEPTH` 和 `RESEARCH_MAX_TOKENS` 配置。Critic 达到预算时停止补查。token 统计依赖模型服务返回的 usage；服务端不返回时无法精确计数。

## 为什么节点是同步函数

当前模型层调用同步 OpenAI 客户端，手写 `delegate` 的子 Agent 并行执行使用 `ThreadPoolExecutor`，CLI 通过 `graph.invoke()` 运行。因此图节点使用普通 `def`。仅把节点声明为 `async def` 不会让内部同步调用变成非阻塞调用。若后续改用异步模型、异步工具和 `graph.ainvoke()`，再相应改造节点。

## 运行与边界

```bash
python main_graph.py "调研 Python 3.13 的主要新特性"
python -m unittest discover -v
```

报告保存在 `reports/YYYYMMDD_HHMMSS_主题_运行ID前8位.md`。当前已配置 LangGraph checkpointer，可从最近完成的节点恢复；Validator 检查材料覆盖和文本匹配，不做深层事实判断。具体恢复边界见 [step8.md](step8.md)。
