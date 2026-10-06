# Harness：Research Agent 的运行与评测约定

Harness 是围绕 Agent 的测试、运行和观察层。它不替代内层 ReAct，也不替代外层 LangGraph，而是负责验证流程是否按预期运行。

## 一、Harness 关注什么

- 输入是否正确进入 Planner。
- 子问题是否被放入 `frontier`。
- frontier 中的任务是否批量并行执行。
- 子 Agent 的成功、失败和重试是否被记录。
- findings 是否保留来源、查询和子问题归属。
- Critic 的路由是否符合预算。
- 报告是否保存到 `reports/`。
- 引用校验和最终状态是否可追踪。

## 二、核心运行轨迹

一次运行至少应记录：

```text
run_id
topic
plan
frontier_before
research_batch
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

### 并行执行

- Planner 生成多个子问题。
- `research` 一次把多个查询传给 `run_subagents()`。
- 并发数不超过 `SUBAGENT_MAX_WORKERS`。
- 结果顺序仍与任务顺序对应。

### 失败处理

- 单个子 Agent 失败不会使整批任务崩溃。
- 失败原因进入结果和报告。
- 后续版本应支持有限次数自动重试。

### 路由

- `stop` 进入 `write`。
- `continue` 回到 `research`。
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
- 多个子任务显示同一批次并行执行。
- `reports/` 中出现新的 Markdown 文件。
- 报告末尾包含运行状态和引用校验。

## 六、后续扩展

Harness 后续可以增加：

- JSONL 运行轨迹。
- 每个节点的耗时和 token 统计。
- 失败任务重试次数。
- 报告质量评分。
- 不同模型和提示词的对比评测。
