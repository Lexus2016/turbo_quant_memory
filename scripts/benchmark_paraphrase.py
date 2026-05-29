"""Paraphrased-query retrieval benchmark — the unbiased complement to the
self-retrieval harness.

Queries come from an EXTERNAL fixture of LLM-paraphrased developer questions that
deliberately AVOID the gold item's own wording (different words, mixed languages).
This measures real retrieval, not verbatim near-duplicate matching, and so closes
the bias of benchmark_retrieval_global (whose queries were verbatim spans, which
favour pure vector and the paraphrase-trained model).

Compares pure dense vector vs the production vector-first GATED hybrid on the SAME
paraphrased queries, over the real project corpora (read-only safe copies).

    uv run python scripts/benchmark_paraphrase.py <fixture.json>

Fixture: [{"project_id": "...", "gold_id": "...", "query": "..."}, ...]
Keep the fixture local/gitignored — it references real note content.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_retrieval_global import _safe_copy  # noqa: E402
from benchmark_retrieval_quality import aggregate, hit_at_k, reciprocal_rank  # noqa: E402

TOP_K = 10


def _case(gold: str, ranked: list[str]) -> dict:
    return {
        "rr": reciprocal_rank(ranked, gold),
        "hit@1": hit_at_k(ranked, gold, 1),
        "hit@3": hit_at_k(ranked, gold, 3),
        "hit@5": hit_at_k(ranked, gold, 5),
    }


def run(fixture: list[dict]) -> tuple[dict, dict]:
    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context, semantic_search_impl

    by_project: dict[str, list[dict]] = defaultdict(list)
    for entry in fixture:
        by_project[entry["project_id"]].append(entry)

    vector_cases: list[dict] = []
    hybrid_cases: list[dict] = []
    for pid, entries in by_project.items():
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            repo = tmp_path / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            _safe_copy(pid, home)
            env = {
                "TQMEMORY_HOME": str(home),
                "TQMEMORY_PROJECT_ROOT": str(repo),
                "TQMEMORY_PROJECT_ID": pid,
                "TQMEMORY_PROJECT_NAME": pid,
            }
            _, store = build_runtime_context(cwd=repo, environ=env)
            index = RetrievalIndex(store)
            index.sync_project()
            for entry in entries:
                gold = str(entry["gold_id"])
                query = entry["query"]
                vector_ids = [str(r["item_id"]) for r in index.find_similar(query, "project", limit=TOP_K)]
                hybrid_ids = [
                    str(item["item_id"])
                    for item in semantic_search_impl(
                        query, scope="project", limit=TOP_K, cwd=repo, environ=env
                    ).get("items", [])
                ]
                vector_cases.append(_case(gold, vector_ids))
                hybrid_cases.append(_case(gold, hybrid_ids))
    return aggregate(vector_cases), aggregate(hybrid_cases)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", help="path to the paraphrased-query fixture JSON")
    args = parser.parse_args()
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))

    vector, hybrid = run(fixture)
    print(f"paraphrased queries: {vector['cases']}")
    print(
        f"vector : hit@1={vector['hit@1']:.0%} hit@3={vector['hit@3']:.0%} "
        f"hit@5={vector['hit@5']:.0%} mrr={vector['mrr']:.3f}"
    )
    print(
        f"hybrid : hit@1={hybrid['hit@1']:.0%} hit@3={hybrid['hit@3']:.0%} "
        f"hit@5={hybrid['hit@5']:.0%} mrr={hybrid['mrr']:.3f}"
    )
    print(
        f"delta(hybrid-vector): mrr={hybrid['mrr']-vector['mrr']:+.3f} "
        f"hit@5={(hybrid['hit@5']-vector['hit@5'])*100:+.1f}pp"
    )


if __name__ == "__main__":
    main()
