# 来源追踪改动与复跑记录（2026-10-19）

本记录跟进 [首次试跑](first-evaluation.md)。原始产物位于被 Git 忽略的 `eval/results/provenance_20261019_v2/`，汇总由 `eval/score.py` 生成。题集仍为 `v0.1-topics`，预期子问题尚未人工校准，因此本轮仍是诊断性复跑，不是正式质量基线。

8 题全量复跑时的 Writer JSON 解析尚未包含当前工作区的容错改进（代码围栏、嵌入 JSON 和大小写 ID 兼容）；因此 4/8 的综合回答数不能代表当前解析逻辑。随后只对 q001 做了当前逻辑的点检，不能据此推断另外 7 题。

随后用当前工作区逻辑单独复跑 q001，产物在 `eval/results/provenance_20261019_final/raw/q001.json`：生成了 1 段综合回答，报告中没有 `file:` 证据，4 个子问题中 2 个有 finding。该样本证明新解析路径和本地来源过滤可以工作，但不能推断另外 7 题的表现。该次状态 token 为 46,237，超过配置的 20,000 上限，说明研究预算检查在一个已启动的查询内不能阻止超额使用。

## 实现改动

- `agent/hooks.py` 将网页和 arXiv 搜索输出拆成逐条来源 finding，保留 URL、标题和摘要；文件读取结果记录为 `file:path#Lx-Ly`。工具错误不再进入证据，RAG 继续保留索引返回的文件行号。
- `agent/langgraph/lg_nodes.py` 的报告会按子问题呈现来源证据、缺口和可选的模型综合回答。综合回答只接受输入中存在且属于同一子问题的来源 ID，并保留证据摘录供复核。来源格式与 finding 自动校验不等于事实忠实性验证。
- `agent/langgraph/lg_builder.py` 在报告阶段过滤外部主题中的项目文件命中，避免 RAG 因题集内容自匹配而把本项目评测文档当成外部研究证据。明确询问本项目、仓库或其文档时，仍保留本地来源。
- `eval/score.py` 将原先含义不清的 `tool_source_matches` 改为 `citation_finding_matches`，明确它只表示报告来源能在 finding 中找到。

## 复跑结果

- 8/8 题流程状态为 `done`，报告均非空。
- 161 条报告引用均能对应到 finding，均使用 URL 或文件定位符。这个 100% 是结构对应率；来源页面是否存在、是否支持论断尚未人工核验，因此引用准确率和 `citation_traceability` 仍不可计算。
- Planner 共生成 34 个子问题，其中 11 个有 finding（32.4%）。这只是 Agent 自己生成的子问题材料诊断，不是题集预期覆盖度。
- 8 题全部以研究预算硬停止结束。多题只完成了第一个子问题查询；总状态 token 计数为 263,396，不能直接当作服务商账单用量。
- 新版报告对 4/8 题生成了“综合回答”；其余题保留来源证据与信息缺口。综合回答仍需改进并人工检查。
- 计分器标记 `completion_rate=1.0`、`citation_accuracy=unavailable`、`information_coverage=pending_review`、`token_cost=partial`、`average_steps=unavailable`、`reflection_effectiveness=partial`；`evaluation_complete=false`。

## 后续重点

1. 处理研究预算分配：当前单个子问题的 token 用量可超过 20,000 上限，导致后续子问题完全没有机会研究。需要在同一固定题集上设计并比较总预算与单查询预算，再决定默认值。
2. 让 Writer 稳定为每个有材料的子问题输出综合回答，同时保留可核验的来源；当前只有 4/8 题生成该段。
3. 人工校准题集预期子问题、逐条核对来源存在性和论断支持，再报告质量指标。

本轮使用模型与运行配置记录在 `eval/results/provenance_20261019_v2/manifest.json`；结果目录不入库，需在本机保留以复算本轮分数。
