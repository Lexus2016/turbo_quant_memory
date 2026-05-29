"""Global retrieval-quality benchmark across ALL real tqmemory projects.

The single-project benchmark is a toy; this one runs on the user's actual
accumulated memory across every project, for a realistic picture.

Protocol (safe, read-only on real data, no new dependencies):
  * For each real project under ~/.turbo-quant-memory/projects, COPY only its
    `notes/` and `markdown/` into an isolated temp home. Secrets are NEVER copied
    or touched; the original store is never mutated.
  * Build a fresh index in the temp home, then for each note/block derive a query
    MECHANICALLY from its own text (first wordy sentence, short cue) — gold = that
    item id.
  * Rank with two systems and record the gold rank:
      vector  : pure dense-vector NN (RetrievalIndex.find_similar)
      hybrid  : dense + BM25 fused via RRF (semantic_search)
  * Report Hit@1/@3/@5 + MRR per project and globally, plus the hybrid-vector delta.

Runs incrementally and resumably (results merged into the global report after each
project) so it can be processed "bit by bit".

    uv run python scripts/benchmark_retrieval_global.py list
    uv run python scripts/benchmark_retrieval_global.py run --projects id1,id2 --limit-queries 80
    uv run python scripts/benchmark_retrieval_global.py run --all --max-blocks 1500

HONESTY: queries come from the gold item's own text, so absolute Hit@k is inflated
by lexical overlap. The unbiased signal is the hybrid-vector DELTA (identical
queries for both systems) and the failure cases (gold missing from top-k).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # allow sibling import
from benchmark_retrieval_quality import (  # noqa: E402
    aggregate,
    extract_query,
    hit_at_k,
    reciprocal_rank,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = Path.home() / ".turbo-quant-memory"
REAL_PROJECTS = REAL_HOME / "projects"
BENCHMARK_DIR = PROJECT_ROOT / "benchmarks"
GLOBAL_JSON = BENCHMARK_DIR / "retrieval_quality_global.json"
GLOBAL_MD = BENCHMARK_DIR / "retrieval_quality_global.md"

TOP_K = 10
METRIC_KEYS = ("hit@1", "hit@3", "hit@5", "mrr")


def list_projects() -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    if not REAL_PROJECTS.exists():
        return projects
    for path in sorted(REAL_PROJECTS.iterdir()):
        if not path.is_dir():
            continue
        name = ""
        manifest = path / "manifest.json"
        if manifest.exists():
            try:
                name = str(json.loads(manifest.read_text(encoding="utf-8")).get("project_name", ""))
            except Exception:  # noqa: BLE001
                name = ""
        notes_dir = path / "notes"
        blocks_dir = path / "markdown" / "blocks"
        projects.append(
            {
                "id": path.name,
                "name": name,
                "notes": len(list(notes_dir.glob("*.json"))) if notes_dir.exists() else 0,
                "blocks": len(list(blocks_dir.glob("*.json"))) if blocks_dir.exists() else 0,
            }
        )
    return projects


def _safe_copy(project_id: str, home: Path) -> None:
    """Copy only notes/ + markdown/ + manifest into an isolated home. Secrets and
    the prebuilt retrieval index are deliberately excluded."""
    src = REAL_PROJECTS / project_id
    dst = home / "projects" / project_id
    dst.mkdir(parents=True, exist_ok=True)
    for sub in ("notes", "markdown"):
        source = src / sub
        if source.exists():
            shutil.copytree(source, dst / sub)
    manifest = src / "manifest.json"
    if manifest.exists():
        shutil.copy(manifest, dst / "manifest.json")


def _case(gold: str, ranked_ids: list[str]) -> dict[str, Any]:
    return {
        "rr": reciprocal_rank(ranked_ids, gold),
        "hit@1": hit_at_k(ranked_ids, gold, 1),
        "hit@3": hit_at_k(ranked_ids, gold, 3),
        "hit@5": hit_at_k(ranked_ids, gold, 5),
    }


def eval_project(meta: dict[str, Any], *, limit_queries: int, max_blocks: int | None) -> dict[str, Any]:
    if max_blocks is not None and meta["blocks"] > max_blocks:
        return {"id": meta["id"], "name": meta["name"], "skipped": f"blocks>{max_blocks}"}

    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context, semantic_search_impl

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        _safe_copy(meta["id"], home)
        env = {
            "TQMEMORY_HOME": str(home),
            "TQMEMORY_PROJECT_ROOT": str(repo),
            "TQMEMORY_PROJECT_ID": meta["id"],
            "TQMEMORY_PROJECT_NAME": meta["name"] or meta["id"],
        }
        _, store = build_runtime_context(cwd=repo, environ=env)
        index = RetrievalIndex(store)
        index.sync_project()

        items: list[tuple[str, str]] = []
        for note in store.list_notes("project"):
            items.append((str(note["note_id"]), str(note.get("content", ""))))
        for block in store.list_markdown_blocks():
            items.append((str(block["block_id"]), str(block.get("content_raw", ""))))
        items.sort(key=lambda pair: pair[0])

        vector_cases: list[dict[str, Any]] = []
        hybrid_cases: list[dict[str, Any]] = []
        for gold, text in items:
            if len(hybrid_cases) >= limit_queries:
                break
            query = extract_query(text)
            if not query:
                continue
            vector_ids = [str(r["item_id"]) for r in index.find_similar(query, "project", limit=TOP_K)]
            hybrid_ids = [
                str(item["item_id"])
                for item in semantic_search_impl(
                    query, scope="project", limit=TOP_K, cwd=repo, environ=env
                ).get("items", [])
            ]
            vector_cases.append(_case(gold, vector_ids))
            hybrid_cases.append(_case(gold, hybrid_ids))

    if not hybrid_cases:
        return {"id": meta["id"], "name": meta["name"], "skipped": "no_queries"}

    vector = aggregate(vector_cases)
    hybrid = aggregate(hybrid_cases)
    return {
        "id": meta["id"],
        "name": meta["name"],
        "queries": len(hybrid_cases),
        "vector": vector,
        "hybrid": hybrid,
        "delta": {key: round(hybrid[key] - vector[key], 4) for key in METRIC_KEYS},
    }


def _global_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if "queries" in r]
    total = sum(r["queries"] for r in scored)
    if total == 0:
        return {"projects": len(scored), "queries": 0}

    def weighted(system: str, key: str) -> float:
        return sum(r[system][key] * r["queries"] for r in scored) / total

    return {
        "projects": len(scored),
        "queries": total,
        "vector": {key: round(weighted("vector", key), 4) for key in METRIC_KEYS},
        "hybrid": {key: round(weighted("hybrid", key), 4) for key in METRIC_KEYS},
        "delta": {
            key: round(weighted("hybrid", key) - weighted("vector", key), 4) for key in METRIC_KEYS
        },
    }


def _render_md(report: dict[str, Any]) -> str:
    g = report["global"]
    lines = [
        "# Global Retrieval Quality Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        "",
    ]
    if g.get("queries"):
        lines += [
            f"- **{g['projects']}** projects, **{g['queries']}** real queries, top-{TOP_K}.",
            f"- Global vector: Hit@1 {g['vector']['hit@1']:.1%} · Hit@5 {g['vector']['hit@5']:.1%} · MRR {g['vector']['mrr']:.3f}",
            f"- Global hybrid: Hit@1 {g['hybrid']['hit@1']:.1%} · Hit@5 {g['hybrid']['hit@5']:.1%} · MRR {g['hybrid']['mrr']:.3f}",
            f"- **Hybrid - vector delta (MRR): {g['delta']['mrr']:+.3f}**",
            "",
            "| Project | Queries | vec MRR | hyb MRR | ΔMRR | vec Hit@5 | hyb Hit@5 |",
            "|---|--:|--:|--:|--:|--:|--:|",
        ]
        for r in sorted(report["results"], key=lambda x: x.get("queries", 0), reverse=True):
            if "queries" not in r:
                continue
            lines.append(
                f"| {r['name'] or r['id']} | {r['queries']} | {r['vector']['mrr']:.3f} | "
                f"{r['hybrid']['mrr']:.3f} | {r['delta']['mrr']:+.3f} | "
                f"{r['vector']['hit@5']:.0%} | {r['hybrid']['hit@5']:.0%} |"
            )
        skipped = [r for r in report["results"] if "skipped" in r]
        if skipped:
            lines += ["", "## Skipped", ""]
            lines += [f"- {r['name'] or r['id']}: {r['skipped']}" for r in skipped]
    else:
        lines.append("No scored projects yet.")
    lines += [
        "",
        "> Queries are derived from each gold item's own text (lexical overlap inflates "
        "absolute Hit@k). The unbiased signal is the hybrid-vector ΔMRR and the misses.",
    ]
    return "\n".join(lines) + "\n"


def _write(report: dict[str, Any]) -> None:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    GLOBAL_MD.write_text(_render_md(report), encoding="utf-8")


def _load_report() -> dict[str, Any]:
    if GLOBAL_JSON.exists():
        try:
            return json.loads(GLOBAL_JSON.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"generated_at": datetime.now(UTC).isoformat(), "results": [], "global": {}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all real projects with data counts")
    run = sub.add_parser("run", help="run the benchmark over selected projects")
    run.add_argument("--projects", default="", help="comma-separated project ids")
    run.add_argument("--all", action="store_true", help="run every project")
    run.add_argument("--limit-queries", type=int, default=100, help="max queries per project")
    run.add_argument("--max-blocks", type=int, default=None, help="skip projects with more md blocks")
    run.add_argument("--force", action="store_true", help="re-run projects already in the report")
    args = parser.parse_args()

    projects = list_projects()
    if args.cmd == "list":
        print(f"{'project_id':18} {'notes':>6} {'blocks':>7}  name")
        for p in sorted(projects, key=lambda x: x["notes"] + x["blocks"], reverse=True):
            print(f"{p['id']:18} {p['notes']:>6} {p['blocks']:>7}  {p['name']}")
        print(f"\ntotal: {len(projects)} projects")
        return

    wanted_ids = set(filter(None, args.projects.split(","))) if args.projects else set()
    selected = projects if args.all else [p for p in projects if p["id"] in wanted_ids]
    if not selected:
        print("No projects selected. Use --all or --projects id1,id2 (see `list`).")
        return

    report = _load_report()
    done = {r["id"] for r in report["results"]}
    for meta in selected:
        if meta["id"] in done and not args.force:
            print(f"skip (done): {meta['name'] or meta['id']}")
            continue
        print(f"running: {meta['name'] or meta['id']} (notes={meta['notes']} blocks={meta['blocks']}) ...", flush=True)
        result = eval_project(meta, limit_queries=args.limit_queries, max_blocks=args.max_blocks)
        report["results"] = [r for r in report["results"] if r["id"] != meta["id"]] + [result]
        report["generated_at"] = datetime.now(UTC).isoformat()
        report["global"] = _global_summary(report["results"])
        _write(report)
        if "queries" in result:
            print(
                f"  -> queries={result['queries']} "
                f"vec_mrr={result['vector']['mrr']:.3f} hyb_mrr={result['hybrid']['mrr']:.3f} "
                f"ΔMRR={result['delta']['mrr']:+.3f}",
                flush=True,
            )
        else:
            print(f"  -> skipped: {result['skipped']}", flush=True)

    g = report["global"]
    if g.get("queries"):
        print(
            f"\nGLOBAL: projects={g['projects']} queries={g['queries']} "
            f"vec_mrr={g['vector']['mrr']:.3f} hyb_mrr={g['hybrid']['mrr']:.3f} ΔMRR={g['delta']['mrr']:+.3f}"
        )
    print(f"report: {GLOBAL_MD}")


if __name__ == "__main__":
    main()
