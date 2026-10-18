# RAG 对标：my_agent 与 DeepResearch

> 核对范围：2026-10-18 本地代码。参考项目为 `D:\project\DeepResearch`；本文描述代码实现，不代表已验证其服务部署状态、知识库实际内容或线上效果。

## 1. 目标与定位

本项目 Step 9 已有可用的**项目文档词法检索基线**。对标目标是让 Agent 稳定检索指定的本地知识、引用可核对，并用独立题集证明检索和回答质量。是否引入向量库，应由题集中的召回问题决定。

参考项目的 RAG 流程是：文档解析 → 分块 → 百炼 Embedding → Qdrant 存储；查询时同时运行向量检索与 BM25，去重后交给 Researcher。代码入口见 [摄取](../../DeepResearch/research_engine/rag/ingest.py)、[存储](../../DeepResearch/research_engine/rag/store.py)、[检索](../../DeepResearch/research_engine/rag/retriever.py)和 [Researcher](../../DeepResearch/research_engine/agents/researcher.py)。

## 2. 实现对照

| 环节 | 本项目现状 | DeepResearch 代码现状 | 对标判断 |
| --- | --- | --- | --- |
| 知识来源 | 默认扫描 `README.md`、`TODO.md`、`docs/`、`eval/`；可在构建命令中指定项目内路径 | `DocumentIngester` 接受文件或单层目录；仓库中未找到供普通用户直接调用的摄取 CLI / 当前 Web 上传接口 | 本项目应先确定自己的知识文件目录和收录规则 |
| 格式 | `.md`、`.txt` | PDF、DOCX、Markdown、TXT | 多格式按真实资料需求逐个增加 |
| 切块 | 最多 30 行、约 1100 字符，重叠 3 行；记录原文件行号 | 按空行分段，合并到配置的约 800 字符；`chunk_overlap=100` 已配置，但当前 `chunk_text()` 未使用该参数 | 保留本项目的精确行号来源；若引入 PDF/DOCX，另设计页码或段落定位 |
| 索引与存储 | `rag/index.json` 本地快照，记录文件 SHA-256；BM25 查询 | 文本块及向量存于 Qdrant；BM25 查询时从 Qdrant 取文本，使用 `rank_bm25` 建索引 | 本项目暂不需要额外服务；规模和语义召回出现明确瓶颈再扩展 |
| 中文检索 | 汉字双字切分 + 英文词 | 中文使用 jieba；不可用时按字降级 | 用中文同义改写题实测，不能只按分词方案推断优劣 |
| 检索组合 | BM25，最多返回 5 段 | 向量召回 + BM25；先放向量结果，再补未重复的 BM25 结果，最多 5 段；重排默认关闭 | 参考项目当前是简单合并，并非已验证的 RRF 或学习排序 |
| 来源与引用 | `file:相对路径#L起始-L结束`；命中段落作为独立 finding | `rag:文件名`；摄取 payload 有文档 ID 和块序号，但 Researcher 对外使用文件名 | 本项目的文件与行号更方便复核；多格式扩展时保持这种可定位性 |
| 故障处理 | 缺索引、索引过期直接报错；零命中返回空结果 | `RetrieveResponse` 区分零命中、未配置、Qdrant/Embedding 失败及部分后端失败 | 可借鉴结构化故障状态，避免把失败误认为无相关资料 |
| 评测 | Step 9 有检索功能测试；现有公开资料研究题无本地 RAG gold 目标 | 有检索评测器；其 `retrieval_hit_rate` 用期望关键词是否出现在 Top-k 文本中判命中 | 两边都不能仅凭功能存在声称检索效果；本项目优先建立文档级 gold 题集 |

本项目实现依据：[索引构建与检索](index.py)、[Agent 工具](../tools/rag_search.py)、[Step 9 说明](../docs/step9.md)。参考项目配置见 [config.py](../../DeepResearch/config.py)，故障响应见 [response.py](../../DeepResearch/research_engine/rag/response.py)，检索评测见 [retrieval_eval.py](../../DeepResearch/research_engine/eval/retrieval_eval.py)。

## 3. 知识目录应该怎样建

“知识目录”有两个含义：**资料存放目录**和**索引文件**。当前 `rag/index.json` 是生成的索引，不是手工维护的知识清单；默认收录范围由 `rag/index.py` 的 `DEFAULT_INPUTS` 决定。新增资料不会自动进入索引，构建命令显式给路径时还会替换默认收录范围。

建议下一步在 `rag/knowledge/` 放真正希望 Agent 回答的本地资料，并将它加入默认收录范围。目录中的 `README.md` 可说明资料主题、来源和更新规则，供人维护；Agent 查找具体证据仍应使用 `rag_search`，不能把目录说明当成完整事实库。完成此改动前，如需同时索引新资料与现有文档，可运行：

```powershell
python -m rag.index build README.md TODO.md docs eval rag/knowledge
```

目前只允许项目内的 Markdown/TXT；不要把密钥、个人隐私或未经筛选的外部资料直接加入索引。`rag/index.json` 已被 Git 忽略，应由源文件重建。

## 4. 推荐推进顺序与验收

1. **固定知识范围。** 增加 `rag/knowledge/` 与默认收录配置；提供列出已收录文件、块数和重建时间的命令或输出。验收：新增、修改、删除资料后，重建结果与实际文件一致；过期索引会明确提示。
2. **建立 RAG 题集。** 从本地资料选 20～30 个问题，人工标注可接受的来源文件与支持答案的段落/行号，包含中文同义改写、跨文件问题和应无答案的问题。冻结题集后再调检索参数。验收：报告 Recall@3/5、MRR 或首个正确命中排名、零答案误命中，并保存每题检索结果；评测口径与 [七项主指标](../eval/metrics.md)保持一致。
3. **校验端到端回答。** 检查 Agent 是否真的调用 RAG、答案是否引用正确片段、是否把资料外推成无依据结论。引用存在性与论断忠实性分开记录；不能用“引用了文件名”代替事实核对。
4. **按误差升级检索。** 若词法检索在同义词、跨语言或概念性提问上持续漏召回，先做查询改写或混合检索实验；再考虑 Embedding 与 Qdrant。新方案必须在同一题集上超过现有 BM25 基线，并记录 API 成本、延迟及故障率。
5. **按资料需求扩展格式。** 确有 PDF/DOCX 资料时加入解析；为 PDF 保留页码，为 DOCX 保留标题/段落定位。解析失败、扫描件无可抽取文本、重复导入及文档更新需要明确处理。

## 5. 对标时应保留的边界

- RAG 索引是静态文档检索，不是长期对话记忆；研究过程的 checkpoint 也不等于知识库。
- 参考项目的 Qdrant 和 Embedding 增加外部服务与调用成本；其 BM25 同样依赖 Qdrant 中已有文本。引入同类架构前先证明本项目确有这类需求。
- 两个项目的“检索命中率”只有在同一知识集、相同查询、相同 gold 来源和 Top-k 口径下才能比较。当前不能从各自代码直接得出谁的检索质量更高。
