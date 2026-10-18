# Step 9：本地文档 RAG 工具

本步给 Research Agent 增加**项目文档检索**：先把允许的 Markdown/TXT 文档切片并建立持久化索引，研究时由 `rag_search` 取回相关片段，模型再根据片段生成回答。检索结果包含 `file:相对路径#L起始-L结束`，可以回到原文件核对。索引是本地快照，不是对话记忆，也不会自动抓取互联网内容。

## 使用

在项目根目录执行：

```powershell
python -m rag.index build
python -m rag.index search "PostgreSQL checkpoint 中断恢复" --top-k 3
python main_graph.py "本项目的 checkpoint 如何恢复中断研究？" --backend memory
```

默认索引 `README.md`、`TODO.md`、`docs/` 和 `eval/` 内的 Markdown/TXT。也可以指定项目内的文件或目录：

```powershell
python -m rag.index build docs eval/metrics.md
```

索引存于 `rag/index.json`，已被 `.gitignore` 排除。文档改动后重新运行 `build`；`rag_search` 发现索引中的文档已变动或删除，会拒绝返回过期片段并提示重建。可以直接调用工具：

```python
from tools import run_tool

print(run_tool("rag_search", '{"query":"长期记忆和 checkpoint 的区别","top_k":3}'))
```

## 实现与边界

- `rag/index.py` 用固定行数和字符预算切片，保留原文件行号；中英文共用同一分词函数，中文用连续双字切分、英文按词切分，再用 BM25 排序。`top_k` 最大 5。这个版本是**词法检索 RAG**，没有嵌入模型、向量数据库或重排器；同义改写与跨语言召回较弱。
- `tools/rag_search.py` 注册为主 Agent 与子 Agent 工具，只负责取回材料。返回的 JSON 包含片段文本、分数、具体来源和索引时间；模型负责把材料写成答案。没有命中时返回空 `hits`，应说明证据不足。
- `agent/hooks.py` 把每个命中片段分别记为 `ResearchFinding`，`source` 使用 `file:` 来源而不是笼统的 `rag_search` 工具名。由此报告若引用该来源，可由现有 Validator 匹配到对应 finding；**匹配成功仍不等于事实忠实，需人工复核论断**。
- 建索引只接受项目目录内的 `.md`、`.txt`；拒绝 `.env*`、隐藏目录、`.git`、`eval/results` 和过大的文件。符号链接解析后必须仍在项目内。默认不索引 `reports/`、会话转存或外部项目。
- 这是按文件重建的快照；每次查询会用 SHA-256 检查已索引文件是否变化。新增文件不会被自动发现，需要手动重建。索引只服务单机，不提供跨进程写入协调或跨会话长期记忆。

## 验证与下一步

运行 `python -m unittest tests.test_rag -v` 可检查中文检索、来源行号、过期检测、路径边界和工具接线。“PostgreSQL checkpoint 中断恢复”可检索到 `docs/step8.md`，“七项评测指标”可检索到 `eval/README.md`；具体文档与片段数以本地 `build` 输出为准。

下一步若要对标 [评测七项指标](../eval/metrics.md)，需要另建含项目内文档问题与人工检索目标的 RAG 题集，评估 top-k 命中、回答覆盖及引用忠实性。现有 8 道公开资料研究题不应直接用来声称本地 RAG 命中率。向量检索和长期记忆可分别在检索基线稳定后扩展。
