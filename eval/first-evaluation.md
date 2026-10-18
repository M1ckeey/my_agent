# 首轮评测记录（2026-10-18）

本轮运行了 [题集](dataset.jsonl) 中全部 8 题。题目已经用户审核保留，但预期子问题与参考说明仍待逐项校准（题集版本 `v0.1-topics`），所以这是**首次试跑结果，不是已完成人工质量复核的正式基线**。原始状态见 [raw 目录](results/first_20261018/raw/)，机器汇总见 [summary.json](results/first_20261018/summary.json)，配置见 [manifest.json](results/first_20261018/manifest.json)。

下一轮的评价口径已固定在 [七项指标契约](metrics.md)。本轮已有数字不能直接代替缺失的引用准确率、人工预期覆盖度和 Critic 轮次。

运行配置：`deepseek-v4-flash`，内存 checkpoint，外层默认上限 20 步 / 20000 状态 token，子 Agent 最多 5 步；本次只向 Agent 传入题目原文，没有提供参考说明。工作区有未提交改动，具体代码修订和启动时间以 manifest 为准。

## 运行结果

| 题目 | 流程状态 | Planner 子问题有 finding / 总数 | 引用检查记录 | 耗时 |
| --- | --- | ---: | ---: | ---: |
| q001 Python 3.13 | done | 1 / 4 | 8 | 92.23 秒 |
| q002 日食与月食 | done | 4 / 4 | 10 | 59.22 秒 |
| q003 SQLite / PostgreSQL | done | 2 / 5 | 12 | 66.37 秒 |
| q004 BM25 / 向量检索 | done | 1 / 5 | 10 | 90.78 秒 |
| q005 RAG / 微调 | done | 1 / 5 | 10 | 110.62 秒 |
| q006 链式思维 | done | 1 / 5 | 10 | 94.52 秒 |
| q007 向量存储估算 | done | 2 / 3 | 13 | 83.63 秒 |
| q008 Qdrant / Milvus | done | 1 / 5 | 10 | 74.27 秒 |

- **流程完成率：8/8**，含义仅为 `status=done` 且报告非空。
- **Planner 子问题材料检查：13/36**。这是 Validator 对 Agent 自己生成的子问题检查是否存在关联 finding；不是题集中的 `expected_subquestions` 人工覆盖评分，也不证明 finding 相关或正确。
- **引用检查记录：83 条**。83 条都能在 findings 中匹配到同名工具来源，但来源名均为 `web_search`、`arxiv_search` 等工具级标识；具体来源引用为 **0 条**。因此真正的来源可追溯率记为“未能计算”，不能写作 100%。
- **运行耗时合计：671.64 秒**。ResearchState 记录的 `token_used` 合计 205829；它是流程内部累计值，不等同于服务商账单用量。
- 题集预期子问题覆盖率、论断事实支持率：**未评分**，等待人工逐项复核。

## 抽查与问题定位

1. 当前默认写作器 `agent/langgraph/lg_nodes.py::_default_write` 将 finding 按 Planner 子问题拼入 Markdown，而非综合写出有证据边界的研究结论。报告非空不能视为研究问题已得到回答。q008 的后 4 个 Planner 子问题均输出“信息不足”，因此该题没有完成要求的同条件比较方案。
2. q004–q008 的运行日志均出现至少一次子 Agent 达到 5 步上限后失败；主流程仍进入 `done`。失败没有被单独写进 ResearchState 的结构化状态，影响完成率的解释。
3. q005 的一条 arXiv 搜索返回 409592 篇命中，首条是机器人操作论文，与 RAG / 微调题无关；q006 也出现极宽泛的 arXiv 结果。这暴露出查询构造或结果筛选问题，学术题不能仅靠“调用了 arXiv”算有论文证据。
4. q007 报告中能找到 `307200000` 字节、`307.2` MB、`292.96875` MiB 的正确原始向量计算；这只是该题一个可人工核对的局部结果，不代表内存与延迟分析已经合格。

## 下一轮改进顺序

1. 先让研究报告综合回答原题，并把每条关键论断关联到具体网页、论文或文件，而非仅引用工具名。
2. 保存子 Agent 成败和每个 Planner 子问题的状态；重新设定 Planner 拆分规模、研究 token 预算与子 Agent 步数，使“完成”能暴露部分失败。
3. 对 arXiv 查询增加精确字段或短语策略，并对标题、年份和主题做结果筛选。
4. 人工校准题集 `expected_subquestions` 与参考说明，再基于同一题集和明确口径重跑，建立可比较的正式基线。

复跑命令：

```powershell
python eval/run.py --output-dir eval/results/<new-run-name>
python eval/score.py --output-dir eval/results/<new-run-name>
```
