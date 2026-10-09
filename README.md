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

详见 [docs/step1.md](docs/step1.md)。

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
compact
```

详见 [docs/step2.md](docs/step2.md)。

### Step 3：Research Agent 与子 Agent

目标是处理需要多方面资料的研究任务。

主 Agent 会先调用 `plan_research` 拆分主题，再通过 `delegate` 并行启动多个子 Agent。每个子 Agent 拥有独立上下文，只返回最终结论，主 Agent 再负责汇总。

这一阶段实现了：

- 研究主题拆分和复杂度判断
- 基于线程池的并行子 Agent
- 子 Agent 上下文隔离
- 子任务数量、并发数和步数限制
- 失败子任务的结果记录

详见 [docs/step3.md](docs/step3.md)。

### Step 4：Hook 扩展与上下文管理

目标是把工具结果从普通文本变成可去重、可截断、可压缩的 `ResearchFinding`，为后续的上下文压缩和 LangGraph 状态管理打基础。

这一阶段实现了：

- 主 Agent 和子 Agent 的结构化 findings
- 按来源、查询和内容去重
- finding 数量和单条内容长度限制
- `ContextManager` 整理研究材料

详见 [docs/step4.md](docs/step4.md)。

### Step 5：外层 LangGraph 编排

目标是在手写 ReAct 外层增加完整的研究流程图：

```text
plan → research → critic → revise/research → write → validate
```

这一阶段实现了：

- 独立的 `ResearchState`
- LangGraph 外层研究流程
- Critic 条件边和补充查询
- frontier 批量提交和线程池并行执行
- `plan`、`research`、`critic`、`write`、`validate` 节点日志
- 写作前 findings 压缩
- 报告来源校验
- 报告自动保存到 `reports/`

详见 [docs/step5.md](docs/step5.md)。

### Step 6：会话级上下文压缩

目标是让 Agent 在长任务中持续工作，同时保留被压缩内容的恢复路径。

这一阶段实现了：

- 五步会话压缩流程：转存、归档、旧结果替换、结果适配和历史摘要
- 工具调用与工具结果配对保护
- transcript 和大型工具结果落盘
- 按 `run_id` 和序号保存 transcript，避免多次压缩互相覆盖
- API 上下文超限后的 reactive compact
- 可由模型主动调用的 `compact` 工具
- 压缩次数、字符数和转存结果等运行指标

详见 [docs/step6.md](docs/step6.md)。

### Step 7：研究流程质量与预算控制

目标是让外层研究流程可追踪、可限额，并能检查研究证据是否覆盖报告。

这一阶段实现了：

- findings 与 `subquestion_id` 的关联
- 失败子 Agent 的有限次数重试和失败原因记录
- 外层研究步数、子 Agent 步数、重试次数和 token 统计
- `RESEARCH_MAX_DEPTH` 与 `RESEARCH_MAX_TOKENS` 硬预算
- 基于 LLM 的 Critic 和确定性回退
- 子问题覆盖和来源支持校验
- Writer 按子问题归类研究材料

详见 [docs/step7.md](docs/step7.md)。

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
python main_graph.py "调研 Python 3.13 的主要新特性"
```

不带参数时进入交互模式：

```bash
python main.py
```

使用外层 LangGraph 研究流程：

```bash
python main_graph.py
```

每次通过 `main_graph.py` 完成的研究，会自动保存为 Markdown 文件，位置为：

```text
reports/YYYYMMDD_HHMMSS_主题.md
```

`TAVILY_API_KEY` 只在使用网页搜索时需要。通过修改 `LLM_BASE_URL`，也可以接入其他 OpenAI 兼容模型服务。

研究流程还支持以下运行参数：

```env
RESEARCH_MAX_DEPTH=20
RESEARCH_MAX_TOKENS=20000
SUBAGENT_MAX_RETRIES=1
SUBAGENT_MAX_STEPS=5
SUBAGENT_MAX_WORKERS=4
```

其中 `RESEARCH_MAX_DEPTH` 和 `RESEARCH_MAX_TOKENS` 是外层研究预算；任一预算耗尽后，流程会停止继续研究。`SUBAGENT_MAX_RETRIES` 控制单个子 Agent 的失败重试次数。

运行自动化测试：

```bash
python -m unittest discover -v
```

## 目录结构

```text
.
├── main.py                    命令行入口
├── main_graph.py              LangGraph 外层研究流程入口
├── prompts.py                 Agent 和规划器提示词
├── requirements.txt           Python 依赖
├── agent/
│   ├── loop.py                主 Agent 循环
│   ├── llm.py                 LLM 调用、流式响应和重试
│   ├── state.py               对话消息与执行轨迹
│   ├── research_state.py      外层 LangGraph 状态
│   ├── graph.py               外层研究流程图和 Validator
│   ├── compactor.py           五步会话级上下文压缩
│   ├── context.py             ResearchFinding 去重和材料压缩
│   ├── hooks.py               Agent 生命周期 Hook
│   └── subagent.py            子 Agent 和并行执行
├── tools/
│   ├── __init__.py            工具注册表和统一执行入口
│   ├── compact.py             主动请求上下文压缩
│   ├── calculator.py          安全计算器
│   ├── web_search.py          网页搜索
│   ├── arxiv_search.py        arXiv 搜索
│   ├── read_file.py           文件读取
│   ├── plan.py                研究计划工具
│   └── delegate.py            子 Agent 调度工具
├── tests/
│   ├── test_context.py        研究材料压缩测试
│   ├── test_compactor.py      会话压缩测试
│   ├── test_graph.py          外层图和子问题归属测试
│   ├── test_subagent_retry.py 子 Agent 重试测试
│   ├── test_budget.py         预算硬闸测试
│   ├── test_critic.py         Critic 路由测试
│   └── test_validator.py      覆盖和来源支持校验测试
├── docs/
│   ├── architecture.md        系统架构
│   ├── harness.md             Harness 运行说明
│   ├── step1.md               最小 ReAct Agent
│   ├── step2.md               多工具与统一接口
│   ├── step3.md               Research Agent 与子 Agent
│   ├── step4.md               Hook 扩展与上下文管理
│   ├── step5.md               外层 LangGraph 编排
│   ├── step6.md               会话级上下文压缩
│   └── step7.md               研究流程质量与预算控制
├── reports/                   自动生成的研究报告
├── .task_outputs/             转存的大型工具结果
└── .transcripts/              压缩前的会话记录
```

## 当前限制

- 对话状态只在单次运行中保存，尚未接入 checkpoint 和中断恢复
- token 使用量依赖模型服务返回 `usage.total_tokens`；服务端不返回时无法精确统计
- Critic 的语义判断失败时会回退到确定性规则
- 子 Agent 当前只会重试同一任务，不会根据失败原因自动改写查询
- Validator 主要做材料覆盖和文本匹配，尚未判断事实、数字和来源之间的深层语义关系
- 尚未接入 LangGraph checkpoint、完整运行轨迹和固定主题质量评测
- 尚未接入 RAG 知识库和跨会话长期记忆

