# Step 6：会话级上下文压缩

Step 4 的 `ContextManager` 管理的是结构化研究发现，Step 6 进一步管理 Agent 的完整对话历史。它处理用户消息、assistant 回复、`tool_use` 和 `tool_result`，让长任务在有限上下文中持续运行。

## 目标

上下文压缩需要满足三个条件：

- 优先执行低成本、可恢复的操作；
- 被压缩的内容仍然可以从磁盘恢复；
- 压缩不能破坏工具调用和工具结果的配对关系。

## 五步压缩流程

每次模型调用前，`ConversationCompactor.prepare()` 按固定顺序执行：

```text
tool_result_budget
    ↓
snip_compact
    ↓
micro_compact
    ↓
fit_tool_results
    ↓
compact_history
```

### 1. tool_result_budget

检查最新一批工具结果。结果过大时，将完整内容写入 `.task_outputs/tool-results/`，上下文只保留路径和预览。

### 2. snip_compact

消息数量超过上限时，将完整历史保存到 `.transcripts/`，保留开头消息、最近消息和归档标记。裁剪时会保护 assistant 的工具调用与对应的 tool 结果。

### 3. micro_compact

上下文仍然过大时，替换较早的工具结果。替换内容会先落盘，消息中保留可恢复路径。

### 4. fit_tool_results

如果仍然超限，从最大的工具结果开始转存，并保留有限预览，直到接近目标大小。

### 5. compact_history

确定性压缩后仍然超限时，调用 `LLM_SUMMARY_MODEL` 生成事实摘要。摘要保留当前用户请求、已完成工作、关键文件、约束和剩余任务。模型调用失败时回退到确定性摘要。

## 两个增强机制

### reactive_compact

字符数只是估算。如果模型服务仍返回 `prompt_too_long` 或类似错误，主循环会保存 transcript，压缩旧历史，并最多重试一次。

### 主动 compact

模型可以调用 `compact` 工具，在一个工作阶段完成后主动释放历史空间。主循环会先执行当前批次的全部工具，再进行压缩，避免留下不完整的工具调用记录。

## 当前实现

- `agent/compactor.py`：会话级压缩器、可恢复存储和压缩指标。
- `agent/loop.py`：每次模型调用前自动压缩，处理 API 超限补救和主动压缩。
- `tools/compact.py`：主动压缩工具。
- `tests/test_compactor.py`：验证结果转存、消息配对、摘要回退和指标记录。

压缩指标保存在 `ConversationCompactor.stats`，包括压缩次数、前后字符数、归档消息数、转存结果数和 reactive compact 次数。每个压缩器实例都有独立的 `run_id`，文件按以下结构保存，不会因为同一运行中的多次压缩互相覆盖：

```text
.transcripts/<run_id>/compact_001.json
.task_outputs/tool-results/<run_id>/001_<tool_call_id>.txt
```

## 边界

Step 6 只负责单次运行中的会话上下文。跨会话记忆、LangGraph checkpoint、统一 token 预算和长期知识库属于后续阶段。

## Step 6 收尾

Step 6 已完成会话级压缩、主动压缩、超限补救、LLM 摘要回退、可恢复文件保存和基础指标记录。下一阶段进入 Step 7，处理统一 token 预算与 checkpoint 恢复。
