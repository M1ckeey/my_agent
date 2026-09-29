# Research Agent 待办

## 当前路线

- [x] 建立结构化研究发现（ResearchFinding）
  - [x] 定义统一的数据结构
  - [x] 主 Agent 在工具执行后记录 finding
  - [x] 子 Agent 结论保留来源和任务信息
- [x] 实现 ContextManager
  - [x] 按来源、查询和内容去重
  - [x] 限制 finding 数量和内容长度
  - [ ] 写报告前接入 LLM 摘要压缩
- [x] 加入 Hook 系统
  - [x] 生命周期事件注册和触发
  - [x] 默认 PostToolUse finding 记录
  - [x] 默认 Stop finding 整理
- [ ] 用 LangGraph 编排外层流程
  - [ ] 定义研究流程状态
  - [ ] 实现 plan → research → critic → write → validate
  - [ ] 加入条件边和预算控制
- [ ] 将子 Agent 改成独立 ReAct 子图
- [ ] 增加 RAG 知识库
- [ ] 增加长期记忆和跨会话检索
- [ ] 补充自动化测试和评测样例

## 进度

- [x] 第一项：定义基础 ResearchFinding，并接入主 Agent 工具结果记录

