# 阶段四：Hook 扩展与上下文管理

阶段四开始处理研究结果的生命周期，并加入 Hook 扩展机制。目标是把工具返回的原始文本转换成结构化 finding，再对这些 finding 做去重、限长和数量控制，为后续的摘要压缩与 LangGraph 状态管理打基础。

## 一、为什么需要 findings

原始 ReAct 循环里，工具结果只作为字符串追加到 `messages`：

```text
assistant(tool_call)
tool("搜索结果原文")
```

这种格式适合继续对话，但不方便回答下面的问题：

- 这条信息来自哪个工具或来源？
- 它属于哪个研究任务？
- 是否已经出现过相同结果？
- 是否应该截断、合并或压缩？

因此，`AgentState` 增加了 `findings`，与 `messages` 和 `steps` 并列保存。

## 二、ResearchFinding

`agent.state.ResearchFinding` 是一条结构化研究发现：

```python
@dataclass
class ResearchFinding:
    content: str
    source: str
    source_type: str
    query: str = ""
    task_id: str = ""
    confidence: float = 0.5
```

当前主 Agent 和子 Agent 在执行工具后都会创建 finding：

- `content`：工具返回的内容
- `source`：当前先记录工具名，例如 `web_search`
- `source_type`：工具类型
- `query`：本次工具调用的参数
- `task_id`：子 Agent 对应的任务
- `confidence`：后续用于排序和筛选，当前使用默认值 `0.5`

主 Agent 仍然保留完整的 `messages`，所以 findings 的整理不会破坏原始对话轨迹。

## 三、ContextManager

`agent/context.py` 中的 `ContextManager` 负责整理一轮运行产生的 findings。

默认配置：

```python
ContextManager(
    max_findings=30,
    max_chars_per_finding=2000,
)
```

`compact()` 按以下顺序处理：

1. 按 `source + query + content` 去重。
2. 保留输入顺序。
3. 截断超过长度上限的内容。
4. 截取前 `max_findings` 条结果。

截断只替换 `content`，来源、任务 ID 和置信度等字段都会保留。

## 四、接入主循环

默认 `Stop` Hook 会在 Agent 准备结束时整理 findings：

```python
def compact_findings_hook(state):
    state.findings = ContextManager().compact(state.findings)
```

这形成了两种互补的状态视图：

| 视图 | 用途 |
| --- | --- |
| `messages` | 保留完整的模型对话和工具调用，便于继续推理和调试 |
| `steps` | 保存人类可读的执行轨迹 |
| `findings` | 保存可去重、可压缩、可供报告使用的研究材料 |

## 五、Hook 系统

`agent/hooks.py` 提供事件注册表。主循环只触发事件，扩展逻辑通过 `register_hook()` 注册：

```python
from agent.hooks import register_hook

def log_tool(state, tool_call, output):
    print(f"工具完成：{tool_call.function.name}")

register_hook("PostToolUse", log_tool)
```

当前支持五个事件：

| 事件 | 触发时机 | 典型用途 |
| --- | --- | --- |
| `UserPromptSubmit` | 用户问题进入模型前 | 输入校验、注入上下文 |
| `BeforeModel` | 每次调用模型前 | 上下文预算检查、消息压缩 |
| `PreToolUse` | 工具执行前 | 权限检查、预算控制 |
| `PostToolUse` | 工具执行后 | 记录 finding、日志和指标 |
| `Stop` | Agent 准备结束时 | 整理结果、收尾或要求继续 |

`PreToolUse` 回调返回非 `None` 时会阻止工具执行，并把返回内容作为工具结果回填。`Stop` 回调返回非 `None` 时会向对话追加一条用户消息，让循环继续。其他事件的返回值不参与控制流。

项目内置两个默认 Hook：

- `PostToolUse`：把工具结果写入 `state.findings`
- `Stop`：调用 `ContextManager.compact()` 整理 findings

扩展代码可以独立添加权限、日志、统计和持久化逻辑，不需要修改主循环。
主 Agent 和子 Agent 共用这套注册表，因此权限、日志和预算策略可以统一应用到两层循环。

## 六、当前边界

当前版本完成的是确定性整理，还没有调用 LLM 生成摘要。因此它能控制 findings 的数量和单条长度，但不会减少已经发送给模型的原始 `messages`。

真实的上下文压缩需要下一步实现：

```text
同一来源的多条 finding
        ↓
fast LLM 摘要
        ↓
保留来源和引用信息的压缩 finding
```

压缩结果应在写报告前使用，不能覆盖原始调试轨迹。后续如果要压缩 `messages`，还必须保留完整的 `assistant(tool_calls)` 与对应的 `tool` 消息关系。

## 七、下一步

1. 从网页搜索和 arXiv 结果中提取真实 URL、论文 ID 等来源信息。
2. 按来源合并 finding，并用 fast LLM 生成摘要。
3. 将压缩后的 findings 提供给报告生成节点。
4. 再开始搭建 LangGraph 外层流程。
