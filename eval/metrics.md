# 下一轮评测的七项指标口径

从下一轮运行起，逐题和汇总必须展示下表七项，沿用 [DeepResearch 的指标实现](../../DeepResearch/research_engine/eval/metrics.py) 所区分的**流程、证据、覆盖、检索、成本和停止行为**。借鉴指标维度，不直接套用对方的模型、数据集或目标阈值。每项必须同时记录 `status`（`measured` / `partial` / `pending_review` / `not_applicable` / `unavailable`）、分子分母或原始计数、计算依据；缺数据写 `null` 和原因，不写 0。

| 指标 | 本项目下一轮口径 | 数据与判定者 | 当前准备情况 |
| --- | --- | --- | --- |
| 1. 完成率 | 逐题满足 `status=done`、`error=null`、报告至少 300 字且有至少 2 个 Markdown 标题；汇总为通过题数 / 全部题数。它只排除空转和残缺产物。 | raw 状态与报告，程序自动判定。 | 可算；不能代替质量分。 |
| 2. 引用准确率 | 逐条核对**具体来源**是否真实对应 finding，再判定报告所引论断是否得到该来源支持。分别报告来源存在率与忠实率；没有具体来源、没有引用或未复核时记缺失。 | 报告中的 URL / arXiv ID / 文件路径、对应 finding、人工复核记录；将来可增加独立裁判并人工抽检。 | 当前只引用工具名，故不可算；须先改来源记录与报告引用。 |
| 3. 信息覆盖度 | 用题集的 `expected_subquestions` 作分母，逐项核对 findings 是否提供相关材料；汇总覆盖项数 / 预期项数。Planner 自己生成的子问题另报诊断值，不进入此指标。 | 人工标注的预期子问题与逐项复核；若启用 LLM 裁判，记录模型、提示词版本、理由及人工抽检。 | 题目已审核，预期子问题尚待校准；当前 13/36 不是此指标。 |
| 4. 检索命中率 | 仅在有人工标注的检索目标（如应命中的文档 ID / 可接受来源集合或可核对的关键事实）及检索结果列表时统计命中数 / 目标数，注明 top-k 与匹配规则。关键词或语义近似只作为辅助诊断，不等同于真实相关文档召回。 | 检索目标标注和逐次检索结果。 | 当前无 RAG 题与 gold 检索目标，标为 `not_applicable`；接入 RAG 后启用。 |
| 5. Token 成本 | 优先记录每次模型调用的输入、输出 token，按模型价格和运行日期计算金额；逐题、总计分开。只有 `ResearchState.token_used` 时仅报告内部计数，金额为缺失，不冒充实际账单。 | 模型 API usage、模型名和价格快照；程序自动汇总。 | 当前仅有不完整的状态计数，标为 `partial`。 |
| 6. 平均步数 | 以 Critic 决策轮数为步数，报告逐题轮数与均值；研究查询 `depth` 单列，不混为 Critic 轮数。 | `reflection_log` 或等效持久化事件。 | 当前未保存轮次日志，标为 `unavailable`；`depth` 仅作辅助。 |
| 7. 反思有效性 | 优先区分**主动停止**（预算未触顶且 Critic 决定 stop）与**硬停止**（查询深度或 token 预算触顶），报告主动停止占比及硬停止原因；结合信息覆盖度筛查可能早停，晚停阈值须先基于本项目数据校准。不能用 `critic_signal=stop` 单独认定主动停止。 | 终态预算字段、Critic 信号、轮次日志和覆盖评分；结构性自动判定，质量面人工复核。 | 终态停止类型可部分判定；早停/晚停与“该不该停”仍待复核，标为 `partial`。 |

独立的报告质量复核可按**全面性、事实准确性、相关性、清晰度**四维记录，但不并入七项主指标，也不能让同一模型的自评分替代具体事实核对。DeepResearch 有 [RACE 风格的评分器](../../DeepResearch/research_engine/eval/report_eval.py)，其[主运行代码](../../DeepResearch/research_engine/eval/run.py)目前并未把它纳入七项主表。

## 下一次运行前的入门条件

1. 人工校准并冻结 `expected_subquestions`、`reference_notes` 与题集版本；不根据首轮输出修改正确预期。
2. 报告能引用具体来源，并保留对应 finding；逐条引用复核有可保存的记录。若仍只有工具名，引用准确率必须显示 `unavailable`，整轮只能称试跑。
3. 持久化模型 usage 和 Critic 决策事件，或在七项表中明确标出缺失并说明原因；不得用 `depth` 冒充 Critic 轮数、用状态 token 冒充账单。
4. 检索命中率在本批非 RAG 题上明确标为 `not_applicable`。将来有 RAG 题和检索目标后启用，并与旧轮分开比较。
5. 离线评分保存七项逐题结果、汇总、缺失原因和人工复核记录；只比较同题集版本、同评分口径的轮次。七项中任何适用指标未评分时，不宣称“正式完整基线”。

`score.py` 当前在 `summary.json` 中写入 `metric_version=v2-seven-2026-10-18`、逐题 `seven_metrics`、汇总 `seven_metrics` 和 `evaluation_complete`。复核文件放在本轮 `results/<run>/review/<题目ID>.json`；数组顺序分别对应题集预期子问题与 raw 中 `type=citation` 的引用记录：

```json
{"reviewer":"人工复核者","coverage_checks":[true,false,true],"citation_checks":[{"exists":true,"faithful":true},{"exists":true,"faithful":false}]}
```

只有题目 `annotation_status=calibrated` 后才计算覆盖度；只有引用为具体 `http(s)://`、`arxiv:` 或 `file:` 来源且复核条数完整，才计算引用准确率。来源准确率使用“忠实引用数 / 存在的来源数”，来源存在率另列。未提交完整复核时状态保持缺失，评分脚本会拒绝长度不符或非布尔判断。

首轮的 [8/8 完成、13/36 Planner 材料检查和工具名匹配](first-evaluation.md)只作为问题定位，不能换名套入七项质量分。指标实现参考：[DeepResearch metrics.py](../../DeepResearch/research_engine/eval/metrics.py)、[评测需求](../../DeepResearch/docs/requirements/5-eval-and-metrics.md)。
