"""Build and query a small, reproducible BM25 index for project documents."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "rag" / "index.json"
INDEX_VERSION = 1
DEFAULT_INPUTS = ("README.md", "TODO.md", "docs", "eval")
ALLOWED_SUFFIXES = frozenset({".md", ".txt"})
DENIED_PARTS = frozenset({".git", ".rag", ".task_outputs", ".transcripts", "results", "__pycache__"})
MAX_FILE_BYTES = 1_000_000
MAX_CHUNK_CHARS = 1100
MAX_CHUNK_LINES = 30
OVERLAP_LINES = 3
_SEGMENTS = re.compile(r"[\u3400-\u9fff]+|[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Use CJK bigrams and ASCII words consistently for indexing and queries."""
    tokens: list[str] = []
    for match in _SEGMENTS.finditer(text.lower()):
        part = match.group()
        if "\u3400" <= part[0] <= "\u9fff":
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
                if len(part) <= 8:
                    tokens.append(part)
        else:
            tokens.append(part)
    return tokens


def _safe_file(path: Path) -> Path:
    resolved = path.resolve()
    if ROOT not in resolved.parents:
        raise ValueError(f"只能索引项目目录内的文件：{path}")
    relative = resolved.relative_to(ROOT)
    if any(part in DENIED_PARTS or part.startswith(".env") or part.startswith(".") for part in relative.parts):
        raise ValueError(f"拒绝索引隐藏、密钥或产物路径：{relative.as_posix()}")
    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"只索引 Markdown/TXT：{relative.as_posix()}")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES} 字节：{relative.as_posix()}")
    return resolved


def _collect(inputs: list[str] | None) -> list[Path]:
    found: dict[str, Path] = {}
    for raw in inputs or list(DEFAULT_INPUTS):
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        resolved = candidate.resolve()
        if ROOT not in resolved.parents:
            raise ValueError(f"只能索引项目目录内的路径：{raw}")
        if resolved.is_dir():
            for path in resolved.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                relative = path.relative_to(ROOT)
                if any(part in DENIED_PARTS or part.startswith(".") for part in relative.parts):
                    continue
                checked = _safe_file(path)
                found[checked.relative_to(ROOT).as_posix()] = checked
        else:
            checked = _safe_file(resolved)
            found[checked.relative_to(ROOT).as_posix()] = checked
    return [found[key] for key in sorted(found)]


def _chunks(path: Path, content: str) -> list[dict]:
    lines = content.splitlines()
    relative = path.relative_to(ROOT).as_posix()
    chunks: list[dict] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and end - start < MAX_CHUNK_LINES:
            extra = len(lines[end]) + 1
            if end > start and size + extra > MAX_CHUNK_CHARS:
                break
            size += extra
            end += 1
        text = "\n".join(lines[start:end]).strip()
        if text:
            chunks.append({
                "source": f"file:{relative}#L{start + 1}-L{end}",
                "path": relative,
                "start_line": start + 1,
                "end_line": end,
                "content": text[:MAX_CHUNK_CHARS],
            })
        if end == len(lines):
            break
        start = max(start + 1, end - OVERLAP_LINES)
    return chunks


def build_index(inputs: list[str] | None = None, *, index_path: Path = INDEX_PATH) -> dict:
    """Index selected project Markdown/TXT files and write a local snapshot."""
    files = _collect(inputs)
    if not files:
        raise ValueError("没有可索引的文档")
    chunks: list[dict] = []
    manifest: list[dict] = []
    for path in files:
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            continue
        content = raw.decode("utf-8", errors="replace")
        relative = path.relative_to(ROOT).as_posix()
        manifest.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
        chunks.extend(_chunks(path, content))
    if not chunks:
        raise ValueError("文档为空或没有可索引的文本")
    index = {
        "version": INDEX_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "files": manifest,
        "chunks": chunks,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temporary.replace(index_path)
    return {"files": len(manifest), "chunks": len(chunks), "index": str(index_path)}


def load_index(index_path: Path = INDEX_PATH) -> dict:
    if not index_path.is_file():
        raise FileNotFoundError("RAG 索引不存在；先运行 python -m rag.index build")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if data.get("version") != INDEX_VERSION or not isinstance(data.get("chunks"), list):
        raise ValueError("RAG 索引版本不兼容；请重新构建")
    return data


def stale_files(index: dict) -> list[str]:
    changed = []
    for item in index.get("files", []):
        path = ROOT / item["path"]
        if (not path.is_file() or path.stat().st_size > MAX_FILE_BYTES
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
            changed.append(item["path"])
    return changed


def search(query: str, *, top_k: int = 3, index_path: Path = INDEX_PATH) -> dict:
    """Retrieve ranked chunks with exact file and line provenance."""
    if len(query) > 500:
        raise ValueError("检索词超过 500 字符，请缩短")
    terms = set(tokenize(query))
    if not terms:
        raise ValueError("检索词为空")
    data = load_index(index_path)
    chunks = data["chunks"]
    frequencies = [Counter(tokenize(item["content"])) for item in chunks]
    lengths = [sum(freq.values()) for freq in frequencies]
    average_length = max(1, sum(lengths) / len(lengths))
    document_frequency = Counter(term for freq in frequencies for term in terms if term in freq)
    scores = []
    for index, (freq, length) in enumerate(zip(frequencies, lengths)):
        score = 0.0
        for term in terms:
            count = freq.get(term, 0)
            if not count:
                continue
            idf = math.log1p((len(chunks) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * (count * 2.2) / (count + 1.2 * (0.25 + 0.75 * length / average_length))
        if score > 0:
            scores.append((score, index))
    scores.sort(key=lambda item: (-item[0], chunks[item[1]]["source"]))
    hits = [
        {**chunks[index], "score": round(score, 4)}
        for score, index in scores[:max(1, min(int(top_k), 5))]
    ]
    return {
        "query": query,
        "hits": hits,
        "index_built_at": data["built_at"],
        "stale_files": stale_files(data),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建或查询项目本地 RAG 索引")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="建立索引")
    build.add_argument("paths", nargs="*", help="项目内 Markdown/TXT 路径；默认 README、TODO、docs、eval")
    lookup = commands.add_parser("search", help="检索片段")
    lookup.add_argument("query")
    lookup.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    result = build_index(args.paths or None) if args.command == "build" else search(args.query, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
