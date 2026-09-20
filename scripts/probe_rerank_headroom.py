"""How much could a reranker win? Measures the CEILING before anyone builds one.

The unbiased fixtures show a gap between Hit@1 and Hit@5 (ident: 35% vs 46%), which
is the shape of a reranking opportunity: the right note was retrieved but ranked
below the top. This prints the real ceiling — Hit@k over the whole candidate set,
which is what a PERFECT reranker would convert into Hit@1 — next to two free
baselines, so the cost of a cross-encoder can be weighed against doing nothing and
against a rerank that needs no new dependency.

Baselines, both computed over the production candidate list:
  lexical : rank by how many query tokens the candidate text contains (coverage),
            RRF order breaking ties. Free, no dependency, no model.
  oracle  : gold first whenever it is present anywhere in the candidates. Not an
            algorithm — the upper bound any reranker is competing for.

Read-only: copies the memory home like the other harnesses and never touches the
real store.

    uv run python scripts/probe_rerank_headroom.py benchmarks/fixtures/ident.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_paraphrase import _case
from benchmark_retrieval_global import _safe_copy
from benchmark_retrieval_quality import aggregate

CANDIDATES = 10
_WORD = re.compile(r"[\w'-]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(text)}


def _text_of(item: dict) -> str:
    """Everything the ranker is allowed to see: what semantic_search already returned."""
    parts = [str(item.get("title") or ""), str(item.get("compressed_summary") or "")]
    parts.extend(str(point) for point in item.get("key_points", []) or [])
    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    by_project: dict[str, list[dict]] = defaultdict(list)
    for entry in fixture:
        by_project[entry["project_id"]].append(entry)

    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context, semantic_search_impl

    production: list[dict] = []
    lexical: list[dict] = []
    oracle: list[dict] = []
    present = 0

    for pid, entries in by_project.items():
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home, repo = tmp_path / "home", tmp_path / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            _safe_copy(pid, home)
            env = {
                "TQMEMORY_HOME": str(home),
                "TQMEMORY_PROJECT_ROOT": str(repo),
                "TQMEMORY_PROJECT_ID": pid,
                "TQMEMORY_PROJECT_NAME": pid,
            }
            _, store = build_runtime_context(cwd=repo, environ=env)
            RetrievalIndex(store).sync_project()

            for entry in entries:
                gold = str(entry["gold_id"])
                items = semantic_search_impl(
                    entry["query"], scope="project", limit=CANDIDATES, cwd=repo, environ=env
                ).get("items", [])
                ranked = [str(item["item_id"]) for item in items]
                production.append(_case(gold, ranked))

                query_tokens = _tokens(entry["query"])
                order = sorted(
                    range(len(items)),
                    key=lambda index: (-len(query_tokens & _tokens(_text_of(items[index]))), index),
                )
                lexical.append(_case(gold, [ranked[index] for index in order]))

                if gold in ranked:
                    present += 1
                    oracle.append(_case(gold, [gold] + [i for i in ranked if i != gold]))
                else:
                    oracle.append(_case(gold, ranked))

    total = len(production)
    print(f"fixture={Path(args.fixture).name} cases={total} candidates={CANDIDATES}")
    print(f"gold present in candidates: {present}/{total} = {present / total:.1%}  <- reranking CEILING")
    print(f"{'ranking':>10}  {'hit@1':>6} {'hit@3':>6} {'mrr':>6}")
    for label, cases in (("production", production), ("lexical", lexical), ("oracle", oracle)):
        row = aggregate(cases)
        print(f"{label:>10}  {row['hit@1']:6.1%} {row['hit@3']:6.1%} {row['mrr']:6.3f}")


if __name__ == "__main__":
    main()
