"""Run the fixed research dataset and save one raw artifact per question."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.langgraph.checkpoint import memory_checkpointer
from agent.langgraph.lg_builder import create_graph

EVAL_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _git(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _load_dataset() -> tuple[dict, list[dict]]:
    meta = json.loads((EVAL_DIR / "dataset-meta.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (EVAL_DIR / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != meta["question_count"] or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("题集数量或 ID 与元数据不一致")
    return meta, rows


def _complete_artifact(path: Path, row: dict) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("id") == row["id"] and data.get("topic") == row["topic"] and bool(data.get("finished_at"))


def run(output_dir: Path, limit: int | None = None) -> None:
    meta, rows = _load_dataset()
    if limit is not None:
        rows = rows[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["dataset_version"] != meta["version"]:
            raise ValueError("产物目录的题集版本不一致")
    else:
        manifest = {
            "dataset_version": meta["version"],
            "dataset_status": meta["status"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": _git(["rev-parse", "HEAD"]),
            "working_tree_dirty": bool(_git(["status", "--porcelain"])),
            "model": os.getenv("LLM_MODEL"),
            "llm_base_url": os.getenv("LLM_BASE_URL"),
            "checkpoint_backend": "memory",
            "max_depth": int(os.getenv("RESEARCH_MAX_DEPTH", "20")),
            "max_tokens": int(os.getenv("RESEARCH_MAX_TOKENS", "20000")),
            "question_ids": [r["id"] for r in _load_dataset()[1]],
        }
        _write_json(manifest_path, manifest)
    graph = create_graph(checkpointer=memory_checkpointer(), verbose=False)
    for row in rows:
        path = output_dir / "raw" / f"{row['id']}.json"
        if _complete_artifact(path, row):
            print(f"[skip] {row['id']}", flush=True)
            continue
        thread_id = uuid4().hex
        started = time.monotonic()
        artifact = {
            "id": row["id"],
            "topic": row["topic"],
            "dataset_version": meta["version"],
            "thread_id": thread_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"[run] {row['id']} {row['topic']}", flush=True)
        try:
            state = graph.run(row["topic"], thread_id=thread_id)
            artifact.update(asdict(state))
        except (Exception, SystemExit) as exc:
            artifact.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            try:
                state, pending = graph.get_state(thread_id)
                artifact.update(asdict(state))
                artifact["pending_nodes"] = list(pending)
                artifact["status"] = "failed"
                artifact["error"] = f"{type(exc).__name__}: {exc}"
            except Exception:
                pass
        artifact["elapsed_seconds"] = round(time.monotonic() - started, 2)
        artifact["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(path, artifact)
        print(f"[saved] {row['id']} {artifact['status']} {artifact['elapsed_seconds']}s", flush=True)
    all_rows = _load_dataset()[1]
    statuses = {}
    for row in all_rows:
        path = output_dir / "raw" / f"{row['id']}.json"
        if _complete_artifact(path, row):
            statuses[row["id"]] = json.loads(path.read_text(encoding="utf-8")).get("status", "unknown")
        else:
            statuses[row["id"]] = "missing"
    manifest["statuses"] = statuses
    manifest["completed_count"] = sum(value == "done" for value in statuses.values())
    manifest["failed_count"] = sum(value == "failed" for value in statuses.values())
    manifest["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest_path, manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output_dir, args.limit)


if __name__ == "__main__":
    main()
