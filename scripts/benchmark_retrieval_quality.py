"""Retrieval-quality benchmark for tqmemory.

Measures whether the RIGHT chunk surfaces for a query, at real scale, on the
project's OWN documentation — not a handful of hand-authored pairs.

Protocol (mechanical, reproducible, offline, no new dependencies):
  1. Index the project's real markdown docs into an ephemeral store.
  2. For every prose block, derive a query MECHANICALLY from the block's own
     text (first wordy sentence, capped to a short cue) — gold = that block id.
  3. For each query, rank candidates with TWO systems and record the gold rank:
       - vector  : pure dense-vector nearest neighbours (RetrievalIndex.find_similar)
       - hybrid  : dense + BM25 fused via RRF (semantic_search)
  4. Report Hit@1/@3/@5 + MRR for both, and the hybrid - vector DELTA.

HONESTY: queries are drawn from the gold block's own text, so absolute Hit@k is
inflated by lexical overlap. The load-bearing, UNBIASED signal is the
vector-vs-hybrid DELTA — both systems face identical queries, so the difference
isolates what our RRF design adds over a plain embedder. For an externally
comparable, human-judged number, run a standard BEIR dataset (adds a dependency).

Run on demand (uses the real all-MiniLM embedder):

    uv run python scripts/benchmark_retrieval_quality.py

The pure metric helpers are import-safe and unit-tested in
tests/test_retrieval_quality_metrics.py.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_ROOT / "benchmarks"
REPORT_JSON = BENCHMARK_DIR / "retrieval_quality.json"
REPORT_MD = BENCHMARK_DIR / "retrieval_quality.md"

# Real English docs (skip ru/uk mirrors so near-identical translations do not
# create confusable golds).
REAL_DOCS = [
    "README.md",
    "TECHNICAL_SPEC.md",
    "MEMORY_STRATEGY.md",
    "CLIENT_INTEGRATIONS.md",
    "CHANGELOG.md",
    "AGENTS.md",
]

TOP_K = 10
MIN_QUERY_WORDS = 8
MAX_QUERY_WORDS = 14


def reciprocal_rank(ranked_ids: Sequence[str], gold_id: str) -> float:
    """1/rank of the gold id in the ranked list, or 0.0 if absent."""
    for position, item_id in enumerate(ranked_ids, start=1):
        if item_id == gold_id:
            return 1.0 / position
    return 0.0


def hit_at_k(ranked_ids: Sequence[str], gold_id: str, k: int) -> bool:
    """True if the gold id appears within the top-k of the ranked list."""
    return gold_id in list(ranked_ids)[:k]


def aggregate(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Mean Hit@1/@3/@5 and MRR over per-case metric dicts."""
    total = len(cases)
    if total == 0:
        return {"cases": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "mrr": 0.0}
    return {
        "cases": total,
        "hit@1": sum(1 for c in cases if c["hit@1"]) / total,
        "hit@3": sum(1 for c in cases if c["hit@3"]) / total,
        "hit@5": sum(1 for c in cases if c["hit@5"]) / total,
        "mrr": sum(c["rr"] for c in cases) / total,
    }


def _candidate_sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)  # drop fenced code
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "---", ">")):
            continue
        kept.append(re.sub(r"[*_`#>-]+", " ", stripped))
    joined = " ".join(kept)
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", joined) if part.strip()]


def extract_query(content_raw: str) -> str | None:
    """Deterministically derive a query-like cue from a block: the first wordy
    sentence, capped to MAX_QUERY_WORDS. None if the block has no prose."""
    for sentence in _candidate_sentences(content_raw):
        words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", sentence)
        if len(words) >= MIN_QUERY_WORDS:
            return " ".join(words[:MAX_QUERY_WORDS])
    return None


def _env(home: Path, repo: Path) -> dict[str, str]:
    return {
        "TQMEMORY_HOME": str(home),
        "TQMEMORY_PROJECT_ROOT": str(repo),
        "TQMEMORY_PROJECT_ID": "benchmark-retrieval",
        "TQMEMORY_PROJECT_NAME": "Retrieval Quality Benchmark",
    }


def run() -> dict[str, Any]:
    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context, index_paths_impl, semantic_search_impl

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "memory-home"
        repo = tmp_path / "repo"
        docs = repo / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        env = _env(home, repo)

        copied = 0
        for name in REAL_DOCS:
            src = PROJECT_ROOT / name
            if src.exists():
                shutil.copy(src, docs / name)
                copied += 1
        index_paths_impl(paths=[str(docs)], mode="full", cwd=repo, environ=env)

        _, store = build_runtime_context(cwd=repo, environ=env)
        index = RetrievalIndex(store)
        blocks = store.list_markdown_blocks()

        vector_cases: list[dict[str, Any]] = []
        hybrid_cases: list[dict[str, Any]] = []
        for block in blocks:
            query = extract_query(str(block.get("content_raw", "")))
            if not query:
                continue
            gold = str(block["block_id"])

            vector_ids = [str(r["item_id"]) for r in index.find_similar(query, "project", limit=TOP_K)]
            hybrid_ids = [
                str(item["item_id"])
                for item in semantic_search_impl(
                    query, scope="project", limit=TOP_K, cwd=repo, environ=env
                ).get("items", [])
            ]
            vector_cases.append(_case(query, gold, vector_ids))
            hybrid_cases.append(_case(query, gold, hybrid_ids))

    vector_summary = aggregate(vector_cases)
    hybrid_summary = aggregate(hybrid_cases)
    delta = {
        key: round(hybrid_summary[key] - vector_summary[key], 4)
        for key in ("hit@1", "hit@3", "hit@5", "mrr")
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "docs_indexed": copied,
        "blocks_total": len(blocks),
        "queries": len(hybrid_cases),
        "top_k": TOP_K,
        "vector": vector_summary,
        "hybrid": hybrid_summary,
        "delta_hybrid_minus_vector": delta,
    }


def _case(query: str, gold: str, ranked_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "query": query,
        "rr": reciprocal_rank(ranked_ids, gold),
        "hit@1": hit_at_k(ranked_ids, gold, 1),
        "hit@3": hit_at_k(ranked_ids, gold, 3),
        "hit@5": hit_at_k(ranked_ids, gold, 5),
    }


def _row(label: str, summary: dict[str, Any]) -> str:
    return (
        f"| {label} | {summary['hit@1']:.1%} | {summary['hit@3']:.1%} | "
        f"{summary['hit@5']:.1%} | {summary['mrr']:.3f} |"
    )


def _render_md(report: dict[str, Any]) -> str:
    d = report["delta_hybrid_minus_vector"]
    lines = [
        "# Retrieval Quality Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"- Corpus: **{report['docs_indexed']}** real project docs, "
        f"**{report['blocks_total']}** blocks, **{report['queries']}** mechanically-derived "
        f"queries, top-{report['top_k']}.",
        "- Queries are drawn from each gold block's own text, so absolute Hit@k is "
        "inflated by lexical overlap. The unbiased signal is the **hybrid - vector "
        "delta** (identical queries for both).",
        "",
        "| System | Hit@1 | Hit@3 | Hit@5 | MRR |",
        "|---|---|---|---|---|",
        _row("vector only", report["vector"]),
        _row("hybrid (RRF)", report["hybrid"]),
        "",
        f"**Hybrid - vector delta:** Hit@1 {d['hit@1']:+.1%} · Hit@3 {d['hit@3']:+.1%} · "
        f"Hit@5 {d['hit@5']:+.1%} · MRR {d['mrr']:+.3f}",
        "",
        "> For an externally comparable, human-judged number, run a standard BEIR "
        "dataset through the same stack (adds a benchmark dependency).",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    report = run()
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_render_md(report), encoding="utf-8")
    v, h = report["vector"], report["hybrid"]
    print(f"queries={report['queries']} blocks={report['blocks_total']}")
    print(f"vector : hit@1={v['hit@1']:.1%} hit@5={v['hit@5']:.1%} mrr={v['mrr']:.3f}")
    print(f"hybrid : hit@1={h['hit@1']:.1%} hit@5={h['hit@5']:.1%} mrr={h['mrr']:.3f}")
    print(f"delta  : {report['delta_hybrid_minus_vector']}")
    print(f"report : {REPORT_MD}")


if __name__ == "__main__":
    main()
