# 从 0 到 1 搭建 Research Agent

这是一个用 Python 从零实现的 Research Agent 教学项目。项目直接使用 OpenAI 兼容 API 和原生 tool calling，逐步实现从单工具 Agent 到多工具研究助手的完整链路，不依赖 LangChain 或 LlamaIndex。

## 项目能做什么

Agent 可以：

- 使用计算器完成可靠计算
- 搜索网页和 arXiv 论文
- 读取项目目录内的文件
- 把复杂主题拆成多个研究问题
- 并行派发子 Agent，最后汇总研究结论

核心循环如下：

```text
用户问题
  ↓
模型判断下一步
  ↓
调用工具或派发子任务
  ↓
读取结果并回填上下文
  ↓
继续判断，直到生成最终答案
```

## 学习路线

### Step 1：最小 ReAct Agent

目标是先跑通最基本的 Agent 闭环：

```text
模型决定调用 calculator
→ 程序执行计算
→ 结果作为 tool 消息回填
→ 模型生成答案
```

这一阶段实现了：

- `agent/loop.py`：主循环和停止条件
- `agent/state.py`：消息历史与执行轨迹
- `tools/calculator.py`：基于 AST 白名单的安全计算器
- LLM 流式输出

详见 [step1.md](step1.md)。

### Step 2：多工具与统一接口

目标是让 Agent 能根据任务选择不同工具，并降低新增工具的成本。

这一阶段实现了：

- 原生 OpenAI tool calling
- 统一的 `Tool` 接口和工具注册表
- 根据函数签名和 docstring 自动生成 JSON Schema
- 统一的参数转换和错误回填
- 网页搜索、arXiv 搜索和文件读取

当前工具包括：

```text
calculator
web_search
arxiv_search
read_file
plan_research
delegate
```

详见 [step2.md](step2.md)。

### Step 3：Research Agent 与子 Agent

目标是处理需要多方面资料的研究任务。

主 Agent 会先调用 `plan_research` 拆分主题，再通过 `delegate` 并行启动多个子 Agent。每个子 Agent 拥有独立上下文，只返回最终结论，主 Agent 再负责汇总。

这一阶段实现了：

- 研究主题拆分和复杂度判断
- 基于线程池的并行子 Agent
- 子 Agent 上下文隔离
- 子任务数量、并发数和步数限制
- 失败子任务的结果记录

详见 [step3.md](step3.md)。

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

在项目根目录创建 `.env`：

```env
LLM_API_KEY=你的模型 API Key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash
TAVILY_API_KEY=你的 Tavily Key
```

运行一次任务：

```bash
python main.py "123 * 456 再减去 1000 等于多少"
python main.py "调研 Python 3.13 的主要新特性"
```

不带参数时进入交互模式：

```bash
python main.py
```

`TAVILY_API_KEY` 只在使用网页搜索时需要。通过修改 `LLM_BASE_URL`，也可以接入其他 OpenAI 兼容模型服务。

## 目录结构

```text
main.py              命令行入口
prompts.py           Agent 和规划器提示词
agent/loop.py        主 Agent 循环
agent/llm.py         LLM 调用、流式响应和重试
agent/state.py       对话消息与运行轨迹
agent/subagent.py    子 Agent 和并行执行
tools/               工具实现与注册表
step1.md             最小 ReAct Agent
step2.md             多工具与统一接口
step3.md             Research Agent 与子 Agent
```

## 当前限制

- 对话状态只在单次运行中保存
- 尚未实现通用的上下文压缩
- 子 Agent 失败后不会自动重新调度
- 项目目前缺少正式的自动化测试

