# 首批研究题目候选（题目已审核）

用户已确认这 8 道题目全部保留，并已写入 [dataset.jsonl](dataset.jsonl)。**预期子问题与参考说明仍是待逐项校准的草案，尚未运行 Agent 或评分。**题目按 [选题与校准规则](dataset-selection.md) 覆盖事实梳理、方案对比、学术证据、定量估算和信息不足。下列资料仅供标注者核对可回答性，不要求 Agent 命中指定网址。

所有题目标为 `selection_origin=人工拟题`、`bucket=capability`。现有对话中的真实问题多依赖本项目上下文或缺少固定输入，未冒充可直接复用的真实用户题。

## q001 · 事实梳理 · 中

**题目：** Python 3.13 相比 3.12 有哪些主要变化？哪些新能力仍属实验性？升级已有项目时应重点核对什么？

预期研究方面：

1. 概括语言、标准库及运行时的主要变化。
2. 区分默认可用能力与实验性能力，以及各自的启用条件。
3. 从弃用、移除及兼容性说明提炼升级检查项。

核对入口：[Python 3.13 官方 What’s New](https://docs.python.org/3.13/whatsnew/3.13.html)。审核重点：答案不能把实验性自由线程或 JIT 表述为所有安装方式默认启用。

## q002 · 事实梳理 · 易

**题目：** 日食和月食分别需要什么太阳、地球、月球位置关系？它们对应什么月相，可见范围有何差别？为什么不是每个月都发生？

预期研究方面：

1. 说明两类食的几何位置与月相。
2. 比较地表观测范围及观测条件。
3. 解释月球轨道倾角与交点附近对食发生的限制。

核对入口：[NASA 日月食几何说明](https://science.nasa.gov/eclipses/geometry/)、[NASA 日月食类型](https://science.nasa.gov/eclipses/types/)。审核重点：这是跨领域的基础题，用来检查检索和清晰解释能力。

## q003 · 方案对比 · 中

**题目：** 给单机研究工具保存任务状态和研究结果时，SQLite 与 PostgreSQL 在部署维护、并发读写、事务和备份恢复方面各有什么取舍？什么条件下应考虑从 SQLite 迁移？

预期研究方面：

1. 比较部署与日常维护成本。
2. 比较并发读写和事务隔离的实际约束。
3. 比较备份恢复路径，并给出有条件的迁移判断。

核对入口：[SQLite 隔离说明](https://sqlite.org/isolation.html)、[SQLite 备份 API](https://www.sqlite.org/backup.html)、[PostgreSQL 16 锁机制](https://www.postgresql.org/docs/16/explicit-locking.html)、[PostgreSQL 16 备份与恢复](https://www.postgresql.org/docs/16/backup.html)。审核重点：不预设 PostgreSQL 总是更合适；须说明负载条件。

## q004 · 方案对比 · 中

**题目：** 面向文档检索，BM25 和稠密向量检索分别更适合处理哪些查询？若要在同一批文档上比较它们，应怎样设计数据、相关性标注和评价指标？

预期研究方面：

1. 解释两种方法的检索信号和典型优缺点。
2. 区分精确术语查询与语义相近但词面不同的查询。
3. 设计公平对比所需的语料、查询、相关性标注及指标。

核对入口：[BEIR 论文](https://arxiv.org/abs/2104.08663)。审核重点：不要预设某种方法在所有数据集上胜出；这题检验评估设计，不要求本项目已有 RAG。

## q005 · 学术证据 · 难

**题目：** 根据 2023—2024 年公开的实证研究，知识密集型问答中 RAG 与微调各自改善什么问题？论文的任务、数据、模型与实验设置如何限制结论的适用范围？

预期研究方面：

1. 找到至少两项有实验对照的研究并说明设置。
2. 比较知识更新、任务适配和证据可追溯性等维度。
3. 区分论文直接观察与面向新场景的推断。

核对入口：[Fine-Tuning or Retrieval?](https://arxiv.org/abs/2312.05934)、[Fine Tuning vs. Retrieval Augmented Generation for Less Popular Knowledge](https://arxiv.org/abs/2403.01432)、[Retrieval-Augmented Generation for Large Language Models: A Survey](https://arxiv.org/abs/2312.10997)。审核重点：文献范围与结论可能需要人工再收窄；不要求给出普适赢家。

## q006 · 学术证据 · 难

**题目：** 链式思维提示在哪些任务或实验条件下可能提升或降低模型表现？对照研究分别用了什么模型、任务和基线，能得出多大范围的结论？

预期研究方面：

1. 说明经典正向结果的任务与实验条件。
2. 查找显示负面或混合效果的研究，核对其基线。
3. 解释为何不能把某一实验结果外推到所有模型与任务。

核对入口：[Chain-of-Thought Prompting Elicits Reasoning in Large Language Models](https://arxiv.org/abs/2201.11903)、[Mind Your Step (by Step): Chain-of-Thought can Reduce Performance on Tasks where Thinking Makes Humans Worse](https://arxiv.org/abs/2410.21333)。审核重点：论文题名不是结论本身，需核对正文的任务与数值。

## q007 · 定量估算 · 中

**题目：** 存储 10 万条、每条 768 维的 FP32 向量，原始向量数据需要多少字节、MB 和 MiB？若用于向量检索，为什么仅凭条数和维度不能确定总内存与查询延迟？

预期研究方面：

1. 明确 FP32 每维字节数并展示计算过程及单位换算。
2. 列出索引、元数据、压缩和内存/磁盘放置带来的额外变量。
3. 说明查询延迟还受索引配置、目标召回率、硬件和负载影响。

核对入口：[Qdrant 生产环境检索调优](https://qdrant.tech/documentation/production-operations/vector-search-in-production/)。人工计算校核：`100000 × 768 × 4 = 307200000` 字节，即 307.2 MB、约 292.97 MiB；这是**原始向量**大小，不是完整部署内存。审核重点：有可核对的确定结果，也有必须保留的不确定部分。

## q008 · 信息不足 · 中

**题目：** 只凭 Qdrant 和 Milvus 各自公开的性能材料，能否断言在“10 万条中文文档、相同硬件、相同召回目标”下哪一个查询更快？可以确认什么，还缺少哪些条件或实验？

预期研究方面：

1. 查明公开材料各自的数据集、软件版本、硬件和指标口径。
2. 区分材料能支持的局部结论与不能支持的指定场景排名。
3. 提出同条件对比所需的嵌入模型、索引、过滤、并发、召回率及延迟测量方案。

核对入口：[Qdrant benchmark 说明](https://qdrant.tech/benchmarks/)、[Milvus 2.2 benchmark 测试报告](https://milvus.io/docs/benchmark.md)。审核重点：应允许有条件地报告公开基准结果，同时明确其不能直接证明题目指定场景的排名。

## 审核决定

2026-10-18：用户确认 q001–q008 **全部保留**。题目已进入 `dataset.jsonl`，标注状态为 `topic_approved`。下一步是逐项校准预期子问题与参考说明，随后冻结可运行的基线版本；当前还没有基线结果。
