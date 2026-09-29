# 阶段一：最小 ReAct 循环

阶段一实现了一个能工作的最小 Agent：模型决定是否调用工具，程序执行工具并把结果放回对话，模型再继续处理。

## 核心流程

主入口是 `agent.loop.run_agent`，每轮最多调用一次模型：

1. 把当前 `messages` 和工具定义发给模型。
2. 如果返回 `tool_calls`，执行对应工具，并为每个调用追加一条 `role: "tool"` 消息。
3. 如果没有 `tool_calls`，把消息内容作为最终答案。
4. 最多运行 `max_steps=8` 轮，超出后返回未完成提示。

工具调用由 `tools.run_tool()` 统一处理；模型一轮可以返回多个调用，但主循环会按顺序执行它们。

## calculator

`tools/calculator.py` 中的计算器使用 `ast` 解析表达式，再按白名单求值，不使用 `eval()`。

支持：

- `+ - * / // % **`
- `abs`、`round`、`min`、`max`、`pow`、`sqrt`

幂运算有结果规模上限，过大的表达式会返回错误，避免阻塞进程。

## 运行状态

`agent.state.AgentState` 保存两种视图：

- `messages`：发送给模型的 OpenAI Chat 格式消息。
- `steps`：结构化执行轨迹，包含工具名、参数和结果。

模型的文本通过 `agent.llm.call_llm()` 流式接收。主 Agent 传入回调实时输出，子 Agent 则关闭输出，避免并发打印互相交错。

## 运行示例

```bash
python main.py "123 * 456 再减去 1000 等于多少"
```

带参数运行一次任务；不带参数运行 `python main.py` 会进入交互模式。

当前实现已经扩展为多工具和子 Agent，阶段一文档描述的是主循环的基础机制。

