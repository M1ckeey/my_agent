"""Local RAG retrieval, provenance, and indexing boundaries."""

import json
import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.hooks import _record_finding
from agent.state import AgentState
from rag.index import ROOT, _safe_file, build_index, search, tokenize
from tools import TOOLS
from agent.subagent import SUBAGENT_TOOLS


class LocalRagTests(unittest.TestCase):
    def test_chinese_and_english_tokenization(self):
        terms = tokenize("长期记忆 PostgreSQL checkpoint")
        self.assertIn("长期", terms)
        self.assertIn("记忆", terms)
        self.assertIn("postgresql", terms)
        self.assertIn("checkpoint", terms)

    def test_build_search_and_staleness(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder = Path(temp)
            document = folder / "sample.md"
            document.write_text("# 说明\n\nPostgreSQL checkpoint 可以恢复中断研究。\n", encoding="utf-8")
            index_path = folder / "index.json"
            stats = build_index([str(document)], index_path=index_path)
            self.assertEqual(stats["files"], 1)
            result = search("PostgreSQL 中断恢复", index_path=index_path)
            self.assertEqual(result["stale_files"], [])
            self.assertIn("sample.md#L1-L3", result["hits"][0]["source"])
            self.assertIn("checkpoint", result["hits"][0]["content"])
            document.write_text("# 改过的内容\n", encoding="utf-8")
            self.assertIn("sample.md", search("恢复", index_path=index_path)["stale_files"][0])

    def test_denies_secrets_and_outside_files(self):
        with self.assertRaises(ValueError):
            _safe_file(ROOT / ".env")
        with self.assertRaises(ValueError):
            _safe_file(ROOT.parent / "outside.md")

    def test_tool_registration_and_source_findings(self):
        self.assertIn("rag_search", TOOLS)
        self.assertIn("rag_search", SUBAGENT_TOOLS)
        state = AgentState(task="研究本项目 checkpoint")
        call = SimpleNamespace(function=SimpleNamespace(name="rag_search", arguments='{"query":"checkpoint"}'))
        payload = {"hits": [
            {"source": "file:docs/step8.md#L1-L4", "content": "研究可以恢复"},
            {"source": "file:docs/step8.md#L5-L9", "content": "使用 checkpoint"},
        ]}
        _record_finding(state, call, json.dumps(payload, ensure_ascii=False))
        self.assertEqual(len(state.findings), 2)
        self.assertEqual(state.findings[0].source, "file:docs/step8.md#L1-L4")
        self.assertEqual(state.findings[0].source_type, "rag_search")

    def test_tool_rejects_stale_index(self):
        module = importlib.import_module("tools.rag_search")
        with patch.object(module, "search", return_value={"hits": [], "stale_files": ["docs/step8.md"]}):
            with self.assertRaisesRegex(RuntimeError, "索引已过期"):
                module.rag_search("checkpoint")


if __name__ == "__main__":
    unittest.main()
