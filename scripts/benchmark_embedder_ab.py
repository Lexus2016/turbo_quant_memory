"""A/B the embedding model on real project corpora — pure dense retrieval quality.

Answers ONE question before committing to a 384->1024 dim migration + a ~2GB model
dependency: does BAAI/bge-m3 actually retrieve better than the current multilingual
MiniLM on OUR real, multilingual corpora?

Method (read-only, no index/schema, no migration): for each project, read its real
notes + markdown blocks directly, derive a query per item (same mechanical rule as
the other benchmark), then for EACH model embed items+queries in-memory and rank by
cosine. The verbatim-query bias applies EQUALLY to both models, so the RELATIVE
MRR/Hit@k comparison is fair. Speed is not a concern here.

    uv run python scripts/benchmark_embedder_ab.py <project_id> [<project_id> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_retrieval_quality import extract_query, hit_at_k, reciprocal_rank  # noqa: E402

from turbo_memory_mcp.retrieval_index import EMBEDDING_MODEL_NAME  # noqa: E402

REAL_PROJECTS = Path.home() / ".turbo-quant-memory" / "projects"
MODELS = {"current": EMBEDDING_MODEL_NAME, "bge-m3": "BAAI/bge-m3"}
TOP_K = 10
MAX_QUERIES = 30
# Cap the retrieval pool so bge-m3 CPU embedding stays tractable. Fair because BOTH
# models rank against the SAME capped pool — this is a relative model comparison,
# not an absolute-difficulty measurement.
MAX_POOL_ITEMS = 300


def load_items(project_id: str) -> list[tuple[str, str]]:
    base = REAL_PROJECTS / project_id
    items: list[tuple[str, str]] = []
    notes_dir = base / "notes"
    if notes_dir.exists():
        for f in sorted(notes_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if str(d.get("note_status", "active")) != "active":
                continue
            items.append((str(d.get("note_id") or f.stem), str(d.get("content", ""))))
    blocks_dir = base / "markdown" / "blocks"
    if blocks_dir.exists():
        for f in sorted(blocks_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items.append((str(d.get("block_id") or f.stem), str(d.get("content_raw", ""))))
    return items


def _embed(model, texts: list[str]) -> np.ndarray:
    vectors = model.encode(
        texts, batch_size=32, show_progress_bar=False, normalize_embeddings=True
    )
    return np.asarray(vectors, dtype=np.float32)


def _evaluate(model, items: list[tuple[str, str]], queries: list[tuple[str, str]]) -> dict:
    ids = [iid for iid, _ in items]
    item_vecs = _embed(model, [text for _, text in items])
    query_vecs = _embed(model, [q for _, q in queries])
    cases = []
    for (gold, _), qv in zip(queries, query_vecs, strict=True):
        sims = item_vecs @ qv  # cosine: vectors are normalized
        order = np.argsort(-sims)[:TOP_K]
        ranked = [ids[j] for j in order]
        cases.append(
            {
                "rr": reciprocal_rank(ranked, gold),
                "hit@1": hit_at_k(ranked, gold, 1),
                "hit@5": hit_at_k(ranked, gold, 5),
            }
        )
    n = len(cases) or 1
    return {
        "queries": len(cases),
        "mrr": sum(c["rr"] for c in cases) / n,
        "hit@1": sum(1 for c in cases if c["hit@1"]) / n,
        "hit@5": sum(1 for c in cases if c["hit@5"]) / n,
    }


def main() -> None:
    from sentence_transformers import SentenceTransformer

    project_ids = sys.argv[1:]
    if not project_ids:
        print("usage: benchmark_embedder_ab.py <project_id> [...]")
        return

    loaded = {name: SentenceTransformer(path) for name, path in MODELS.items()}
    print(f"models: {MODELS}\n")

    totals = {name: {"q": 0, "rr": 0.0, "h1": 0.0, "h5": 0.0} for name in MODELS}
    for pid in project_ids:
        items = load_items(pid)[:MAX_POOL_ITEMS]
        queries: list[tuple[str, str]] = []
        for iid, text in items:
            q = extract_query(text)
            if q:
                queries.append((iid, q))
            if len(queries) >= MAX_QUERIES:
                break
        if not queries:
            print(f"{pid}: no queries, skip")
            continue
        line = f"{pid:18} items={len(items):5} q={len(queries):3}"
        for name, model in loaded.items():
            r = _evaluate(model, items, queries)
            line += f" | {name}: mrr={r['mrr']:.3f} h1={r['hit@1']:.0%} h5={r['hit@5']:.0%}"
            totals[name]["q"] += r["queries"]
            totals[name]["rr"] += r["mrr"] * r["queries"]
            totals[name]["h1"] += r["hit@1"] * r["queries"]
            totals[name]["h5"] += r["hit@5"] * r["queries"]
        print(line, flush=True)

    print("\n=== GLOBAL (query-weighted) ===")
    for name in MODELS:
        t = totals[name]
        q = t["q"] or 1
        print(f"{name:9}: queries={t['q']} mrr={t['rr']/q:.3f} hit@1={t['h1']/q:.1%} hit@5={t['h5']/q:.1%}")


if __name__ == "__main__":
    main()
