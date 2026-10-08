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
- [ ] 完善外层研究流程
  - [ ] 让 Critic 使用 LLM 判断证据是否充分
  - [ ] 失败子 Agent 自动重试或重新调度
  - [ ] 接入统一 token 统计和硬闸
  - [ ] 让 Writer 输出干净的最终报告
- [ ] 完善 findings 归属和汇总
  - [ ] 每条 finding 明确关联 `subquestion_id`
  - [ ] 按来源和子问题汇总子 Agent findings
  - [ ] 缺少材料时明确输出“信息不足”
- [ ] 完善 Validator
  - [ ] 检查报告是否覆盖所有子问题
  - [ ] 检查来源是否真正支持对应论断
  - [ ] 检查关键事实和数字的一致性
- [ ] 增加失败任务重试
  - [ ] 识别达到最大步数或工具失败的任务
  - [ ] 将失败任务重新放回 frontier
  - [ ] 限制单个任务的最大重试次数
- [ ] 增加统一预算
  - [ ] 统计外层和子 Agent token 使用量
  - [ ] 增加总研究步数、单任务步数和重试次数上限
  - [ ] 预算触发后停止继续调用模型
- [ ] 增加 LangGraph checkpoint 和中断恢复
  - [ ] 保存运行标识和状态版本
  - [ ] 从最近节点恢复中断任务
- [ ] 建立 Harness 运行轨迹和评测
  - [ ] 记录节点耗时、token、重试次数和报告路径
  - [ ] 增加固定主题的报告质量评测
  - [ ] 增加不同模型和提示词的对比评测
- [x] 保留子 Agent 的手写 ReAct，并与外层 ResearchState 隔离
- [ ] 增加 RAG 知识库
- [ ] 增加长期记忆和跨会话检索
- [ ] 补充自动化测试
  - [ ] 测试三种 Critic 路由
  - [ ] 测试预算触发停止
  - [ ] 测试 findings 去重、压缩和来源校验
  - [ ] 测试并行任务顺序和报告落盘
  - [ ] 测试原有 `run_agent()` 回归

## 进度

- [x] 第一项：定义基础 ResearchFinding，并接入主 Agent 工具结果记录
- [x] 第二项：定义独立的 ResearchState，隔离外层研究流程状态
- [x] 第三项：完成 LangGraph 外层闭环、并行研究、引用校验和报告落盘
- [x] 第四项：整理 docs 文档目录并建立 Harness 说明

