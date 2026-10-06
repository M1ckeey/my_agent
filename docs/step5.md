# Step 5：外层 LangGraph 编排

这一阶段在手写 ReAct 外层增加研究流程图。LangGraph 负责研究流程、状态和路由，内层 Agent 仍然使用现有的手写 ReAct 完成具体任务。

## 一、整体结构

```text
外层 LangGraph：
START → plan → research → critic ─┬─ stop   → write → validate → END
                                  ├─ revise → research
                                  └─ continue → research

内层 ReAct：
调用模型 → 调用工具 → 回填结果 → 再次调用模型 → 返回任务结果
```

外层保存主题、子问题、frontier、findings、报告和引用校验结果。内层只保存当前 Agent 的 `messages`、工具调用和执行轨迹。

## 二、ResearchState

`agent/research_state.py` 定义外层状态：

```python
@dataclass
class ResearchState:
    topic: str
    user_instructions: str
    subquestions: list[ResearchSubQuestion]
    findings: list[ResearchFinding]
    raw_findings: list[ResearchFinding]
    frontier: list[dict]
    depth: int
    max_depth: int
    critic_signal: str
    next_queries: list[dict]
    report: str
    citations: list[dict]
    status: str
```

`frontier` 保存待研究查询，`findings` 保存写作材料，`raw_findings` 保存压缩前的原始材料。外层状态不保存子 Agent 的完整消息历史。

## 三、图节点

```text
START
  ↓
plan
  ↓
research
  ↓
critic
  ├─ continue → research
  ├─ revise   → revise → research
  └─ stop     → write → validate → END
```

| 节点 | 当前职责 |
| --- | --- |
| `plan` | 调用 `plan_research`，生成子问题和 frontier |
| `research` | 收集当前 frontier 的全部查询，并行运行子 Agent |
| `critic` | 根据 frontier 和 `max_depth` 决定继续、补充查询或停止 |
| `revise` | 把 Critic 返回的补充查询放回 frontier |
| `write` | 压缩 findings，按子问题生成 Markdown 报告 |
| `validate` | 检查报告中的来源是否存在于 findings |

## 四、并行研究

`research` 会把当前 frontier 中的全部查询一次性提交给 `run_subagents()`：

1. 收集当前轮次的全部查询。
2. 使用线程池并行运行多个子 Agent。
3. 按任务顺序合并子 Agent 返回的 findings。
4. 清空当前 frontier，并增加外层研究深度。
5. 将研究结果交给 Critic。

运行时会看到类似日志：

```text
[graph] research：并行提交 5 个子任务
[delegate] 5 个子任务并行调研（并发 4，每个最多 5 步）
  · [1] 出发：...
  · [2] 出发：...
  · [2] 完成 · 4 步
  · [1] 完成 · 失败：达到最大步数 5
[delegate] 3/5 成功，共 22 步
```

完成顺序可能不同，这是并行执行的正常表现。`3/5 成功` 表示 5 个子任务中有 3 个完成，失败任务会保留失败原因。

## 五、Critic 的当前行为

当前默认 Critic 是确定性路由器：

- frontier 为空：返回 `stop`
- 达到 `max_depth`：返回 `stop`
- 仍有待处理查询且未超预算：返回 `continue`
- 自定义 Critic 可以返回 `revise` 和补充查询

因此，日志中的：

```text
[graph] critic：stop，findings=42，next_queries=0
```

表示当前没有待处理查询，流程进入写作阶段。它不表示所有子任务都成功，也不表示报告已经通过事实审查。

## 六、写作、压缩和校验

`write` 节点会先调用 `ContextManager.compress()`，再把压缩后的 findings 交给 Writer。原始材料保存在 `raw_findings`。

```text
原始 findings
  ↓
去重、截断、数量限制
  ↓
必要时调用摘要器
  ↓
按子问题生成报告
```

`validate` 当前只做来源存在性校验。例如报告中出现 `web_search`，且 findings 中存在同名来源，就算该引用通过。它还不会判断来源是否真正支持对应论断。

## 七、运行和报告保存

```bash
python main_graph.py "调研 Python 3.13 的主要新特性"
```

运行过程中会打印：

| 日志 | 含义 |
| --- | --- |
| `[graph] plan` | 拆分研究主题 |
| `[graph] research` | 批量提交并行研究任务 |
| `[delegate]` | 子任务数量、并发数、步数和完成状态 |
| `[graph] critic` | 外层路由决定 |
| `[graph] write` | 整理 findings 并生成报告 |
| `[graph] validate` | 执行引用校验 |

每次完成后，报告会自动保存到：

```text
reports/YYYYMMDD_HHMMSS_主题.md
```

保存内容包括报告正文、运行状态、研究步数、findings 数量和引用校验结果。

## 八、当前边界

- Critic 还没有接入 LLM 语义判断。
- 失败子 Agent 会记录原因，但不会自动重试或重新调度。
- Writer 目前主要整理 findings，可能保留研究过程中的自述和中间材料。
- `token_used` 还没有接入统一的 token 统计和硬闸。
- Validator 目前只检查来源是否存在。
- LangGraph 还没有接入 checkpoint 和中断恢复。
- 项目还缺少正式的自动化测试。

下一步应优先完善失败任务重试、LLM Critic 和最终报告 Writer，再加入 checkpoint 与正式测试。

## 九、后续实现计划

迁移计划中的未完成部分归并到这里，后续按以下顺序推进：

1. **完善 findings 归属**
   - 每条 finding 明确关联 `subquestion_id`、查询、来源和置信度。
   - Writer 按子问题和来源整理材料。
   - 缺少材料时明确输出“信息不足”，不复用其他子问题的内容。

2. **加入失败任务重试**
   - Critic 识别达到最大步数或工具失败的子任务。
   - 将失败任务转换为新的 frontier 查询。
   - 限制单个任务的重试次数，避免无限循环。

3. **升级 Critic**
   - 先由代码检查 `max_depth`、任务数量和 token 预算。
   - 预算允许时，再由 LLM 判断证据是否充分。
   - Critic 只返回 `continue`、`revise` 或 `stop` 以及状态增量，不直接修改状态。

4. **增加统一预算**
   - 统计外层和子 Agent 的 token 使用量。
   - 增加总研究步数、单任务步数、重试次数和 token 上限。
   - 预算触发后停止继续调用模型，并保留已有结果。

5. **改进 Writer 和 Validator**
   - Writer 只输出最终报告，不保留提示词、自述和中间推理。
   - Validator 增加子问题覆盖检查、来源支持检查和关键论断检查。

6. **加入 checkpoint 和恢复**
   - 为 LangGraph 配置 checkpoint。
   - 支持中断后从最近节点恢复。
   - 保存运行标识、状态版本和失败原因。

7. **补充自动化测试**
   - 覆盖三种 Critic 路由。
   - 覆盖预算停止和失败任务重试。
   - 覆盖 findings 去重、压缩和来源校验。
   - 覆盖并行任务顺序、报告落盘和原有 `run_agent()` 回归。

RAG 知识库、长期记忆和跨会话检索属于后续阶段，等外层流程、预算和恢复机制稳定后再加入。
