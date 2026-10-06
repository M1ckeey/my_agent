# 阶段三：子 Agent

阶段三加入了 `plan_research` 和 `delegate`，用于处理需要多方面资料的任务。

## 工作流程

主 Agent 根据任务决定是否：

1. 调用 `plan_research`，让规划器生成子问题列表并判断复杂度。
2. 对复杂任务调用 `delegate`。
3. `delegate` 为每个子问题启动一个独立的子 Agent。
4. 主 Agent 收到所有子任务结论后，在下一轮生成最终答案。

`delegate` 的结果直接作为一条工具消息回填，没有额外的总结 Agent。

## 子 Agent 的边界

每个子 Agent 都有独立的消息历史，只能看到自己的任务和工具结果。它可以使用：

```text
web_search、arxiv_search、read_file、calculator
```

它不能调用 `plan_research` 或 `delegate`，因此不会递归派生更多 Agent。子 Agent 不向终端流式输出，只返回最终结论或错误。

## 并行执行

`agent.subagent.run_subagents()` 使用 `ThreadPoolExecutor` 并行运行任务：

- 默认最多 4 个并发线程，可通过 `SUBAGENT_MAX_WORKERS` 调整。
- 默认每个子 Agent 最多 5 轮，可通过 `SUBAGENT_MAX_STEPS` 调整。
- 单次最多派发 8 个任务，可通过 `SUBAGENT_MAX_TASKS` 调整。
- 结果按原任务顺序返回，和实际完成顺序无关。
- 单个任务失败会被记录，其他任务仍可完成。

`delegate` 会把每条结论限制在 2000 字符以内，再交回主 Agent。子 Agent 自己的上下文仍会增长，项目尚未实现上下文压缩。

## 使用建议

每条任务应只描述一个方面，并且写成自包含的一两句话，因为子 Agent 看不到主对话历史。任务过多或描述过长都会增加调用成本，也可能触发模型输出上限。

