"""Calibrate a ranking constant against an unbiased fixture.

retrieval_index.py carries VECTOR_GATE_THRESHOLD = 0.82 with a measured
justification for its EXISTENCE (equal-weight RRF underperformed pure vector) but
none for its VALUE. The two harnesses that run derive queries from the gold item's
own text and saturate, so they cannot resolve it. This sweeps the constant over a
fixture built by build_paraphrase_fixture.py, where there is headroom.

Mechanism (retrieval_index.py): similarity = 1 - distance; when the dense top hit
scores >= the threshold the BM25 lane is SKIPPED. So a LOWER threshold skips BM25
more often (towards pure vector) and a HIGHER one fuses more often. 0.0 = always
skip, > 1.0 = never skip.

The index is built ONCE and every threshold re-runs only the ranking, so a sweep
costs one embed pass rather than one per value.

Targets (--constant):
  gate    retrieval_index.VECTOR_GATE_THRESHOLD
  recency retrieval.RECENCY_BONUS_MAX  (grid in bonus units, not similarity)
  fts     retrieval_index.FTS_LANE_WEIGHT (RRF weight of the BM25 lane; 1.0 = equal)

    uv run python scripts/sweep_vector_gate.py benchmarks/fixtures/ident.json
    uv run python scripts/sweep_vector_gate.py benchmarks/fixtures/ident.json --constant recency
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_paraphrase import TOP_K, _case
from benchmark_retrieval_global import _safe_copy
from benchmark_retrieval_quality import aggregate

GRIDS = {
    "gate": [0.0, 0.60, 0.70, 0.82, 0.90, 0.95, 1.01],
    "recency": [0.0, 0.025, 0.05, 0.10, 0.20],
    "fts": [0.1, 0.3, 0.5, 0.7, 1.0],
}
CURRENT = {"gate": 0.82, "recency": 0.05, "fts": 0.3}
TARGETS = {
    "gate": ("turbo_memory_mcp.retrieval_index", "VECTOR_GATE_THRESHOLD"),
    "recency": ("turbo_memory_mcp.retrieval", "RECENCY_BONUS_MAX"),
    "fts": ("turbo_memory_mcp.retrieval_index", "FTS_LANE_WEIGHT"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    parser.add_argument("--constant", choices=sorted(TARGETS), default="gate")
    parser.add_argument("--grid", type=float, nargs="*", default=None)
    args = parser.parse_args()
    grid = args.grid or GRIDS[args.constant]
    current = CURRENT[args.constant]

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    by_project: dict[str, list[dict]] = defaultdict(list)
    for entry in fixture:
        by_project[entry["project_id"]].append(entry)

    import importlib

    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context, semantic_search_impl

    module_name, attribute = TARGETS[args.constant]
    target = importlib.import_module(module_name)

    per_threshold: dict[float, list[dict]] = {value: [] for value in grid}
    vector_cases: list[dict] = []

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
            index = RetrievalIndex(store)
            index.sync_project()          # the one embed pass the whole sweep shares

            for entry in entries:
                gold = str(entry["gold_id"])
                vector_cases.append(_case(gold, [
                    str(row["item_id"]) for row in index.find_similar(entry["query"], "project", limit=TOP_K)
                ]))

            original = getattr(target, attribute)
            try:
                for value in grid:
                    # Both constants are read as module globals at call time, so setting the
                    # attribute is enough; no re-import and no re-embed.
                    setattr(target, attribute, value)
                    for entry in entries:
                        ranked = [
                            str(item["item_id"])
                            for item in semantic_search_impl(
                                entry["query"], scope="project", limit=TOP_K, cwd=repo, environ=env
                            ).get("items", [])
                        ]
                        per_threshold[value].append(_case(str(entry["gold_id"]), ranked))
            finally:
                setattr(target, attribute, original)

    vector = aggregate(vector_cases)
    print(f"fixture={Path(args.fixture).name} constant={attribute} cases={vector['cases']}")
    print(f"{args.constant:>6}  {'hit@1':>6} {'hit@3':>6} {'hit@5':>6} {'mrr':>6}   vs current")
    print(f"{'vector':>6}  {vector['hit@1']:6.1%} {vector['hit@3']:6.1%} {vector['hit@5']:6.1%} {vector['mrr']:6.3f}   (no BM25 lane)")
    baseline = aggregate(per_threshold[current])["mrr"] if current in per_threshold else None
    for value in grid:
        row = aggregate(per_threshold[value])
        mark = "  <- current" if value == current else (
            f"  mrr {row['mrr'] - baseline:+.3f}" if baseline is not None else "")
        print(f"{value:6.2f}  {row['hit@1']:6.1%} {row['hit@3']:6.1%} {row['hit@5']:6.1%} {row['mrr']:6.3f}{mark}")


if __name__ == "__main__":
    main()
