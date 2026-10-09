# Step 7：研究流程质量与预算控制

Step 7 把 Step 5 和 Step 6 之后分散的研究流程能力整理成一个完整阶段，重点解决研究材料归属、失败任务处理、预算控制、证据判断和报告校验。

## 目标

让外层研究图具备可控的运行边界，并让最终报告能够说明：

- 每条材料属于哪个子问题；
- 子 Agent 是否失败以及是否发生重试；
- 研究消耗了多少步数和 token；
- Critic 为什么继续、补查或停止；
- 哪些子问题缺少材料，哪些来源真正出现在报告中。

## 本阶段完成内容

### 1. Findings 归属

`ResearchFinding` 增加 `subquestion_id`。外层 frontier 携带子问题 ID，research 节点在收到子 Agent 结果后恢复该归属。Writer 优先按 ID 组织报告，减少依赖问题文本匹配。

### 2. 子 Agent 重试

`run_subagents()` 支持有限次数重试：

```text
一次失败 → 重新执行同一子任务 → 成功或达到重试上限
```

结果中保留：

- `retry_count`：实际重试次数；
- `error_history`：每次失败原因；
- 最终答案或最终失败状态。

默认最多重试 1 次，可通过 `SUBAGENT_MAX_RETRIES` 配置。

### 3. 统一预算

`ResearchState` 现在维护：

- `depth` / `max_depth`：外层研究步数；
- `token_used` / `max_tokens`：研究 token 使用量；
- 子 Agent 的步数、重试次数和 token 使用量。

达到任一预算后，Critic 路由会停止继续研究。外层配置项为：

```text
RESEARCH_MAX_DEPTH=20
RESEARCH_MAX_TOKENS=20000
```

LLM 流式响应中的 `usage.total_tokens` 会被记录；服务端不返回 usage 时，该次计数为 0。

### 4. LLM Critic

默认 Critic 会根据主题、子问题、已有 findings 和 frontier 请求模型判断：

```json
{
  "signal": "stop | continue | revise",
  "next_queries": [
    {"subquestion_id": "q1", "query": "补充查询"}
  ]
}
```

预算检查在调用 Critic 前执行。模型调用失败或输出格式错误时，回退到确定性规则。

### 5. Validator

Validator 现在检查两类问题：

- 子问题是否有对应 findings；
- 报告引用的来源是否存在，并且报告内容是否能匹配该来源材料。

校验结果包含 `type`、`exists`、`supported`、`subquestion_id` 和 `note` 字段。

## 相关代码和测试

- `agent/graph.py`：外层研究、Critic、Writer 和 Validator。
- `agent/research_state.py`：研究状态与预算。
- `agent/subagent.py`：并行子 Agent、重试和 token 汇总。
- `agent/llm.py`：流式响应 token 统计。
- `tests/test_graph.py`：子问题归属。
- `tests/test_subagent_retry.py`：重试和失败记录。
- `tests/test_budget.py`：预算硬闸。
- `tests/test_critic.py`：Critic 路由和 JSON 解析。
- `tests/test_validator.py`：覆盖和来源支持校验。

## 验证结果

当前 Step 7 相关测试共 10 项，全部通过；`compileall` 检查通过。

## 边界

Step 7 完成了单次运行内的质量控制和预算控制。LangGraph checkpoint、运行轨迹持久化、固定主题评测、RAG 和长期记忆属于后续阶段。
