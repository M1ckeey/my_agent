# Research Agent 待办

## 当前路线

- [x] 建立结构化研究发现（ResearchFinding）
  - [x] 定义统一的数据结构
  - [x] 主 Agent 在工具执行后记录 finding
  - [x] 子 Agent 结论保留来源和任务信息
- [x] 实现 ContextManager
  - [x] 按来源、查询和内容去重
  - [x] 限制 finding 数量和内容长度
  - [x] 写报告前接入 LLM 摘要压缩
- [x] 加入 Hook 系统
  - [x] 生命周期事件注册和触发
  - [x] 默认 PostToolUse finding 记录
  - [x] 默认 Stop finding 整理
- [x] 建立会话级上下文压缩骨架
  - [x] 大型工具结果转存并保留预览
  - [x] 历史消息归档和 transcript 保存
  - [x] 压缩时保护 tool_use/tool_result 配对
  - [x] API 上下文超限后执行一次 reactive compact
  - [x] 增加主动 `compact` 工具
  - [x] 超限时接入可回退的 LLM 历史摘要
  - [x] 记录压缩次数、字符数、归档数和转存数
  - [x] 使用 run_id 和序号保存 transcript 与工具结果
  - [x] 完成 Step 6 文档和回归测试
- [x] 用 LangGraph 编排外层流程
  - [x] 定义独立的 ResearchState
  - [x] 搭建 plan → research → write 最小图
  - [x] 加入 critic → revise 条件边
  - [x] 接入 write → validate
  - [x] 加入 frontier 批量提交和线程池并行
  - [x] 加入研究节点、写作节点和校验节点日志
  - [x] 自动保存 Markdown 报告到 reports/
  - [x] 加入基于 max_depth 的基础预算控制
- [x] Step 7：研究流程质量与预算控制
  - [x] findings 关联 `subquestion_id`
  - [x] 失败子 Agent 有限次数重试并记录失败原因
  - [x] 统计外层和子 Agent token 使用量
  - [x] 增加研究深度和 token 硬预算
  - [x] 接入 LLM Critic 和确定性回退
  - [x] 检查子问题覆盖和来源支持
  - [x] 增加 Step 7 文档和回归测试
- [x] 增加 LangGraph checkpoint 和中断恢复
  - [x] 使用 PostgreSQL `PostgresSaver` 保存正式运行的图状态；开发和测试使用 `InMemorySaver`
  - [x] 为每次研究生成稳定的 `thread_id`，并提供按该 ID 查看状态、恢复运行的入口
  - [x] 明确短期记忆需要保存的状态：研究进度与同一线程内的对话上下文
  - [x] 恢复运行时重新注入 Planner、Researcher 等服务，验证状态序列化与版本兼容
  - [x] 按查询保存完成进度，恢复时跳过已完成的查询
  - [x] 测试重建图后的恢复、同一线程连续提问，以及不同线程之间的状态隔离
  - [x] 使用 `TEST_POSTGRES_URL` 测试本机 PostgreSQL 断开并重新连接后的恢复
  - [x] 测试独立进程退出或强制终止后的恢复，以及未完成查询的重试边界
  - [x] 保存运行标识和状态版本
  - [x] 从最近节点恢复中断任务
- [ ] 建立 Harness 运行轨迹和评测
  - [ ] 记录节点耗时、token、重试次数和报告路径
  - [x] 写出固定主题评测流程计划，见 `eval/README.md`
  - [ ] 建立并人工校准 5–10 题固定题集，记录题集版本
  - [ ] 逐题保存研究原始产物，支持跳过已完成题目
  - [ ] 离线计算完成率、材料覆盖、来源可追溯和消耗，标明缺失指标
  - [ ] 人工复核论断与引用，保存首份逐题评分和基线报告
  - [ ] 增加不同模型和提示词的对比评测
- [ ] 验证真实研究任务的报告落盘、证据覆盖和引用质量
- [x] 保留子 Agent 的手写 ReAct，并与外层 ResearchState 隔离
- [ ] 增加 RAG 知识库
- [ ] 增加长期记忆和跨会话检索
  - [ ] 区分记忆范围：现有 checkpoint 以 `thread_id` 保存线程内短期状态；长期记忆按 `user_id` 跨线程检索
  - [ ] 为运行入口增加 `user_id`，明确不同用户的记忆隔离规则
  - [ ] 使用独立的 PostgreSQL 记忆表或 LangGraph `PostgresStore`，保存精选结论与用户明确的偏好，不直接把完整 checkpoint 当作长期记忆
  - [ ] 研究结束后提取值得复用的记忆，记录来源、产生时间、所属用户和原始 `run_id`，并提供去重或更新机制
  - [ ] 新研究开始前按用户和当前主题检索相关记忆，供 Planner 参考；复用旧结论时核对来源与时效
  - [ ] 测试同一用户的不同 `thread_id` 能检索相关记忆、不同用户之间隔离，以及过期或无关记忆不会误导研究
- [ ] 补充自动化测试
  - [ ] 测试三种 Critic 路由
  - [ ] 测试预算触发停止
  - [ ] 测试 findings 去重、压缩和来源校验
  - [ ] 测试手写 `delegate` 的并行任务顺序和外层报告落盘
  - [ ] 测试原有 `run_agent()` 回归

## 本轮实施计划

1. [x] Step 7：研究流程质量与预算控制。
2. [x] Step 8：已完成 checkpoint、内存模式、短期记忆和 PostgreSQL 跨连接恢复验证。
3. [x] Step 8 收尾：验证独立进程重启和研究中断后的恢复边界。
4. [ ] Harness：按 `eval/README.md` 记录运行轨迹、建立固定题集和质量基线。
5. [ ] Step 9：增加 RAG 知识库和长期记忆。

## 进度

- [x] 第一项：定义基础 ResearchFinding，并接入主 Agent 工具结果记录
- [x] 第二项：定义独立的 ResearchState，隔离外层研究流程状态
- [x] 第三项：完成 LangGraph 外层闭环、逐查询研究、引用校验和报告落盘；手写 `delegate` 保留并行执行
- [x] 第四项：整理 docs 文档目录并建立 Harness 说明

