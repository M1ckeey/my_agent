# Step 8：PostgreSQL checkpoint 与中断恢复

外层 LangGraph 图现在使用 checkpointer 保存每个节点结束时的状态。开发和测试默认使用 `InMemorySaver`；需要跨进程恢复时使用 `PostgresSaver`。内层 ReAct 子 Agent 的消息历史仍只属于单次子任务。

`lg_builder.py` 在 `builder.compile(checkpointer=...)` 时接入 checkpoint。`checkpoint.py` 负责创建内存或 PostgreSQL saver，`agent/langgraph/main.py` 在 saver 的连接生命周期内运行图。模块级兼容导出 `graph` 也使用内存 saver；正式运行使用 `ResearchGraph` 注入的 saver。

序列化器只允许恢复本项目使用的 `ResearchState`、`ResearchSubQuestion` 和 `ResearchFinding` 类型。读取最早的输入 checkpoint 与最新研究状态均已通过测试。

## 配置

安装 `requirements.txt` 后，为正式运行配置：

```env
RESEARCH_CHECKPOINT_BACKEND=postgres
RESEARCH_DATABASE_URL=postgresql://user:password@localhost:5432/research
```

启动时会调用 `PostgresSaver.setup()` 建立或升级 checkpoint 表。使用专门的研究数据库账号和数据库。未配置后端时默认为内存模式；它只在当前进程存活期间保留状态。

## 运行与恢复

```bash
python main_graph.py "调研 Python 3.13" --backend postgres --thread-id python-research
python main_graph.py --status --backend postgres --thread-id python-research
python main_graph.py --resume --backend postgres --thread-id python-research
```

`thread_id` 标识一个会话。首次运行可省略，程序会生成并打印；后续查看、恢复或继续提问时使用同一个 ID。每次新研究有独立 `run_id`，报告会记录两者及 `state_version`。交互模式在本进程中持续复用同一个线程。已完成的线程可用新主题再次运行；状态只保留最近五轮的主题与报告摘要，供 Planner 参考。不同线程的状态隔离。

需要验证可控的中断时：

```bash
python main_graph.py "调研 Python 3.13" --backend postgres --thread-id demo --pause-after plan
python main_graph.py --status --backend postgres --thread-id demo
python main_graph.py --resume --backend postgres --thread-id demo
```

`--pause-after` 会在指定节点完成并保存 checkpoint 后停止；恢复命令不要再带该参数。运行时意外异常或进程退出后，可用 `--status` 看下一节点，再使用 `--resume`。不能在未完成的线程上直接开始另一个主题。恢复时会重新创建 Planner、Researcher 等服务，不把服务对象写入 checkpoint。

## Research 进度与重试

外层 `research` 每次处理一个查询，节点成功后同时保存 findings、`completed_queries`、剩余 `frontier` 和预算。恢复时不会重新执行已提交 checkpoint 的查询。这样每个查询都有明确的恢复边界，代价是外层查询按顺序执行；单个查询内部仍可使用子 Agent 的工具。

若 Critic 再次提出已完成或已排队的查询，路由会忽略重复项；没有新查询时直接写报告，避免在 `revise` 与 `research` 间空转。

若进程在一个查询执行过程中终止，该查询尚未形成 checkpoint，恢复时可能重试。当前搜索和读取工具是只读操作，因此允许重试。未来若增加写入型工具，必须为该工具加入按 `run_id` 与查询键去重的幂等机制。报告保存也按 `run_id` 查找已有文件，重复恢复不会再写一份报告。

## 测试

在本项目的 `langgraph` Conda 环境运行普通测试。它们使用内存 saver，覆盖暂停后重建图、失败后恢复、线程内连续提问、跨线程隔离和重复查询路由：

```powershell
conda activate langgraph
python -m unittest discover -v
python -m compileall -q agent tools main.py main_graph.py
```

2026-10-16 在 `langgraph` 环境验证：LangGraph 1.1.2、checkpoint 4.0.1、PostgreSQL saver 3.0.5；连接本机 PostgreSQL 16 的独立 `research` 数据库后，32 项测试全部通过，编译检查通过。`requirements.txt` 的最低版本与该环境保持一致。

若有测试数据库，设置 `TEST_POSTGRES_URL` 后运行跨连接恢复集成测试。下面从本地 `.env` 读取 URL，不会在终端打印密码；测试会在该数据库中留下 checkpoint 记录，建议使用可清理的专用测试库：

```powershell
$line = Get-Content .env | Where-Object { $_ -like 'RESEARCH_DATABASE_URL=*' } | Select-Object -Last 1
$env:TEST_POSTGRES_URL = $line.Substring('RESEARCH_DATABASE_URL='.Length)
python -m unittest tests.test_checkpoint -v
Remove-Item Env:TEST_POSTGRES_URL
```

跨连接恢复测试已在本机 PostgreSQL 上通过；它验证关闭并重新打开数据库连接后恢复同一线程。2026-10-16 的测试在本机 `research` 数据库留下了测试线程数据。尚未测试独立进程被强制终止后的恢复，也未运行真实模型研究任务做端到端质量验证。状态版本目前为 1；遇到其他版本会明确报错，后续修改状态结构时需要同步实现迁移。PostgreSQL checkpoint 保存的是外层研究状态，不能恢复一个正在执行的内层 ReAct 调用到中间的 token 或工具步骤。

下一步先增加独立进程中断与恢复的集成测试，确认已提交查询不会重跑、正在执行的查询允许重试、最终报告只保存一次。之后记录节点耗时和 token、重试次数、引用覆盖及报告路径，再按 [评测计划](../eval/README.md) 用固定主题评估报告质量。

接口参考：[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[PostgresSaver README](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres)。
