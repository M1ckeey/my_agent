# Harness：Research Agent 的运行与评测约定

Harness 是围绕 Agent 的测试、运行和观察层。它不替代内层 ReAct，也不替代外层 LangGraph，而是负责验证流程是否按预期运行。

## 一、Harness 关注什么

- 输入是否正确进入 Planner。
- 子问题是否被放入 `frontier`。
- frontier 中的任务是否逐项保存进度，恢复时是否跳过已完成查询。
- 子 Agent 的成功、失败和重试是否被记录。
- findings 是否保留来源、查询和子问题归属。
- Critic 的路由是否符合预算。
- 报告是否保存到 `reports/`。
- 引用校验和最终状态是否可追踪。

## 二、核心运行轨迹

一次运行至少应记录：

```text
run_id
thread_id
topic
plan
frontier_before
research_query
completed_queries
checkpoint_id
subagent_results
critic_signal
findings_count
report_path
citations
status
```

当前终端日志已经覆盖主要节点：

```text
[graph] plan
[graph] research
[delegate]
[graph] critic
[graph] write
[graph] validate
[报告已保存]
```

## 三、最小验收标准

### 研究进度与并行执行

- Planner 生成多个子问题。
- 外层 `research` 每次执行一个查询并保存 checkpoint。
- 手写 Agent 的 `delegate` 可以把多个子任务交给 `run_subagents()`。
- 并发数不超过 `SUBAGENT_MAX_WORKERS`。
- 结果顺序仍与任务顺序对应。

### 失败处理

- 单个子 Agent 失败不会使整批任务崩溃。
- 失败原因进入结果和报告。
- 失败任务按 `SUBAGENT_MAX_RETRIES` 有限次数重试，并保留失败原因。

### 路由

- `stop` 进入 `write`。
- `revise` 经过 `revise` 后回到 `research`。
- 达到 `max_depth` 或预算上限时必须停止。

### 报告

- 报告包含所有已规划子问题。
- 缺少材料的子问题明确写出信息不足。
- 报告自动保存到 `reports/`。
- `validate` 能报告来源存在性结果。

## 四、建议的测试层次

1. **单元测试**：测试 `ResearchState`、`ContextManager`、Critic 路由和文件名生成。
2. **组件测试**：替换 Planner 和 Researcher，验证 graph 节点之间的数据传递。
3. **并行测试**：用可控的假子 Agent 验证多线程执行和结果顺序。
4. **回归测试**：确认原有 `main.py` 和 `run_agent()` 仍能独立运行。
5. **样例评测**：用固定研究主题检查报告覆盖、引用和失败处理。

## 五、运行检查

```bash
python -m compileall -q agent tools main.py main_graph.py
python main_graph.py "调研 Python 3.13 的主要新特性"
```

检查完成后应确认：

- 终端出现 plan、research、critic、write、validate 日志。
- 每个外层查询完成后都能在 checkpoint 中看到进度。
- `reports/` 中出现新的 Markdown 文件。
- 报告末尾包含运行状态和引用校验。

## 六、后续扩展

下一阶段优先实现：

1. 用独立进程验证 PostgreSQL checkpoint：暂停、退出、重启后按相同 `thread_id` 恢复；再模拟研究查询中断，检查已完成查询和报告的幂等行为。
2. 用 JSONL 记录每个节点的开始、结束、耗时、token、重试次数、checkpoint ID 和报告路径；不要写入数据库密码或完整原始消息。
3. 建立固定主题评测集，先检查子问题覆盖、引用支持和失败说明，再比较模型与提示词版本。完成基线后再扩展 RAG 和长期记忆。
