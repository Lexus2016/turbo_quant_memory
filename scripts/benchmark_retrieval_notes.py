"""Retrieval-quality benchmark on REAL memory notes (not markdown docs).

Companion to benchmark_retrieval_quality.py, which measures markdown-block
retrieval on the repo's own documentation and is blind to memory notes. This
one measures NOTE retrieval on the user's real memory store, so ranking
features that only touch notes (e.g. the recency bonus) become measurable.

SAFETY: the real memory home is NEVER touched by a search — a search can
trigger index syncs that re-embed and rewrite LanceDB. The whole
``~/.turbo-quant-memory`` directory is first copied into a TemporaryDirectory
and every runtime call is pointed at the COPY via TQMEMORY_HOME.

Protocol (mechanical, reproducible, offline, no new dependencies):
  1. Copy the real memory home to a temp dir.
  2. Corpus = active notes (note_status == "active") of the GLOBAL scope plus
     the largest project scope.
  3. For every active note, derive a query MECHANICALLY from the note itself:
     its title trimmed to 5-10 words (notes with < 4 title words are skipped).
     Gold = that note_id.
  4. Run semantic_search(query, scope=..., limit=10, source_filter="notes")
     and record the gold rank. tier_filter covers ALL tiers so the deliberate
     default exclusion of `episodic` does not masquerade as a ranking miss.
  5. Report Hit@1/@3/@5 + MRR, with a per-tier breakdown.
  6. Recency A/B: rerun the identical evaluation with
     retrieval.RECENCY_BONUS_MAX monkeypatched to 0.0 (the constant is read
     as a module global at call time inside _recency_bonus) and report both
     metric sets, the delta, and how many gold ranks moved up/down/unchanged.

HONESTY: queries are drawn from each gold note's own title, so absolute
Hit@k is inflated by lexical overlap. The load-bearing, UNBIASED signal is
the recency-on MINUS recency-off DELTA — both runs face identical queries
and indexes, so the difference isolates the bonus's effect on ranking.

Run on demand (uses the cached fastembed model, no network):

    uv run python scripts/benchmark_retrieval_notes.py
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
REPORT_JSON = BENCHMARK_DIR / "retrieval_quality_notes.json"
REPORT_MD = BENCHMARK_DIR / "retrieval_quality_notes.md"

REAL_HOME = Path("~/.turbo-quant-memory").expanduser()

TOP_K = 10
MIN_TITLE_WORDS = 4
MAX_QUERY_WORDS = 10
# Include the global scope plus this many largest project scopes (by note
# count). The live-reported ranking failure was global, so global is the
# primary corpus; the largest project adds scale at modest runtime cost.
MAX_PROJECT_CORPORA = 1

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def reciprocal_rank(ranked_ids: Sequence[str], gold_id: str) -> float:
    """1/rank of the gold id in the ranked list, or 0.0 if absent."""
    for position, item_id in enumerate(ranked_ids, start=1):
        if item_id == gold_id:
            return 1.0 / position
    return 0.0


def gold_rank(ranked_ids: Sequence[str], gold_id: str) -> int | None:
    """1-based rank of the gold id, or None if absent from the list."""
    for position, item_id in enumerate(ranked_ids, start=1):
        if item_id == gold_id:
            return position
    return None


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


def per_tier(cases: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate metrics grouped by note tier."""
    tiers: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        tiers.setdefault(str(case["tier"]), []).append(case)
    return {tier: aggregate(group) for tier, group in sorted(tiers.items())}


def extract_query(title: str) -> str | None:
    """Deterministically derive a query from a note title: the first
    MAX_QUERY_WORDS words. None if the title yields < MIN_TITLE_WORDS words."""
    words = _WORD_RE.findall(title)
    if len(words) < MIN_TITLE_WORDS:
        return None
    return " ".join(words[:MAX_QUERY_WORDS])


def _load_active_notes(notes_dir: Path) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    if not notes_dir.is_dir():
        return notes
    for path in sorted(notes_dir.glob("*.json")):
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupt note JSONs must not break the benchmark
        if note.get("note_status") == "active":
            notes.append(note)
    return notes


def _discover_corpora(home: Path, repo: Path) -> list[dict[str, Any]]:
    """Build (label, scope, environ, notes) corpus descriptors from the copy.

    Project env identity comes straight from each project's manifest.json;
    the explicit TQMEMORY_PROJECT_ID override wins over git resolution.
    """
    corpora: list[dict[str, Any]] = []

    global_notes = _load_active_notes(home / "global" / "notes")
    if global_notes:
        corpora.append(
            {
                "label": "global",
                "scope": "global",
                "environ": {
                    "TQMEMORY_HOME": str(home),
                    "TQMEMORY_PROJECT_ROOT": str(repo),
                    "TQMEMORY_PROJECT_ID": "benchmark-notes",
                    "TQMEMORY_PROJECT_NAME": "Notes Retrieval Benchmark",
                },
                "notes": global_notes,
            }
        )

    projects: list[tuple[int, Path, dict[str, Any], list[dict[str, Any]]]] = []
    projects_root = home / "projects"
    if projects_root.is_dir():
        for project_dir in sorted(projects_root.iterdir()):
            manifest_path = project_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            notes = _load_active_notes(project_dir / "notes")
            if notes:
                projects.append((len(notes), project_dir, manifest, notes))

    projects.sort(key=lambda item: item[0], reverse=True)
    for count, project_dir, manifest, notes in projects[:MAX_PROJECT_CORPORA]:
        project_root = Path(manifest.get("project_root") or repo)
        if not project_root.exists():
            project_root = repo
        corpora.append(
            {
                "label": f"project:{manifest.get('project_name') or project_dir.name}",
                "scope": "project",
                "environ": {
                    "TQMEMORY_HOME": str(home),
                    "TQMEMORY_PROJECT_ROOT": str(project_root),
                    "TQMEMORY_PROJECT_ID": str(manifest["project_id"]),
                    "TQMEMORY_PROJECT_NAME": str(manifest.get("project_name") or project_dir.name),
                },
                "notes": notes,
            }
        )

    return corpora


def _run_evaluation(
    corpus: dict[str, Any],
    *,
    repo: Path,
    tier_filter: Sequence[str],
) -> list[dict[str, Any]]:
    from turbo_memory_mcp.server import semantic_search_impl

    cases: list[dict[str, Any]] = []
    for note in corpus["notes"]:
        title = str(note.get("title") or "")
        query = extract_query(title)
        if not query:
            continue
        gold = str(note["note_id"])
        ranked_ids = [
            str(item["item_id"])
            for item in semantic_search_impl(
                query,
                scope=corpus["scope"],
                limit=TOP_K,
                tier_filter=tier_filter,
                source_filter="notes",
                cwd=repo,
                environ=corpus["environ"],
            ).get("items", [])
        ]
        cases.append(
            {
                "query": query,
                "gold": gold,
                "tier": str(note.get("tier") or "unknown"),
                "rank": gold_rank(ranked_ids, gold),
                "rr": reciprocal_rank(ranked_ids, gold),
                "hit@1": gold in ranked_ids[:1],
                "hit@3": gold in ranked_ids[:3],
                "hit@5": gold in ranked_ids[:5],
            }
        )
    return cases


def _rank_changes(
    cases_on: Sequence[dict[str, Any]], cases_off: Sequence[dict[str, Any]]
) -> dict[str, int]:
    """Count gold-rank movement of the recency-ON run vs the OFF run."""
    off_ranks = {case["gold"]: case["rank"] for case in cases_off}
    up = down = unchanged = 0
    for case in cases_on:
        on_rank = case["rank"]
        off_rank = off_ranks.get(case["gold"])
        if on_rank == off_rank:
            unchanged += 1
        elif off_rank is None or (on_rank is not None and on_rank < off_rank):
            up += 1  # absent with bonus off counts as the worst possible rank
        else:
            down += 1
    return {"up": up, "down": down, "unchanged": unchanged}


def _delta(on: dict[str, Any], off: dict[str, Any]) -> dict[str, float]:
    return {
        key: round(on[key] - off[key], 4) for key in ("hit@1", "hit@3", "hit@5", "mrr")
    }


def run() -> dict[str, Any]:
    import turbo_memory_mcp.retrieval as retrieval

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "memory-home"
        repo = tmp_path / "repo"
        repo.mkdir()
        print(f"copying {REAL_HOME} -> {home} ...")
        shutil.copytree(REAL_HOME, home)

        corpora = _discover_corpora(home, repo)
        all_tiers = ("durable", "episodic", "reference")

        corpus_reports: list[dict[str, Any]] = []
        for corpus in corpora:
            retrieval.RECENCY_BONUS_MAX = 0.05
            cases_on = _run_evaluation(corpus, repo=repo, tier_filter=all_tiers)
            retrieval.RECENCY_BONUS_MAX = 0.0
            try:
                cases_off = _run_evaluation(corpus, repo=repo, tier_filter=all_tiers)
            finally:
                retrieval.RECENCY_BONUS_MAX = 0.05

            summary_on = aggregate(cases_on)
            summary_off = aggregate(cases_off)
            corpus_reports.append(
                {
                    "label": corpus["label"],
                    "scope": corpus["scope"],
                    "notes_active": len(corpus["notes"]),
                    "queries": len(cases_on),
                    "recency_on": {**summary_on, "per_tier": per_tier(cases_on)},
                    "recency_off": {**summary_off, "per_tier": per_tier(cases_off)},
                    "delta_on_minus_off": _delta(summary_on, summary_off),
                    "rank_changes": _rank_changes(cases_on, cases_off),
                }
            )
            print(
                f"{corpus['label']}: queries={len(cases_on)} "
                f"on hit@1={summary_on['hit@1']:.1%} mrr={summary_on['mrr']:.3f} | "
                f"off hit@1={summary_off['hit@1']:.1%} mrr={summary_off['mrr']:.3f}"
            )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "memory_home_source": str(REAL_HOME),
        "top_k": TOP_K,
        "tier_filter": list(all_tiers),
        "recency_bonus_max": 0.05,
        "corpora": corpus_reports,
    }


def _row(label: str, summary: dict[str, Any]) -> str:
    return (
        f"| {label} | {summary['cases']} | {summary['hit@1']:.1%} | "
        f"{summary['hit@3']:.1%} | {summary['hit@5']:.1%} | {summary['mrr']:.3f} |"
    )


def _render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Notes Retrieval Quality Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "Retrieval quality measured on REAL memory notes (a throwaway copy of the "
        "live memory home — the real store is never searched). Queries are derived "
        "mechanically from each gold note's own title (first 5-10 words), so "
        "absolute Hit@k is inflated by lexical overlap. The unbiased signal is the "
        "**recency-on − recency-off delta**: identical queries and indexes in both "
        "runs, so the difference isolates the recency bonus "
        f"(RECENCY_BONUS_MAX={report['recency_bonus_max']}).",
        "",
        f"Top-{report['top_k']}; tier_filter covers all tiers "
        f"({', '.join(report['tier_filter'])}) so the deliberate default exclusion "
        "of `episodic` does not masquerade as a ranking miss.",
        "",
    ]
    for corpus in report["corpora"]:
        d = corpus["delta_on_minus_off"]
        rc = corpus["rank_changes"]
        lines += [
            f"## Corpus: {corpus['label']} (scope={corpus['scope']})",
            "",
            f"**{corpus['notes_active']}** active notes, **{corpus['queries']}** "
            "mechanically-derived queries.",
            "",
            "| Run | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |",
            "|---|---|---|---|---|---|",
            _row("recency ON", corpus["recency_on"]),
            _row("recency OFF", corpus["recency_off"]),
            "",
            f"**Delta (on − off):** Hit@1 {d['hit@1']:+.1%} · Hit@3 {d['hit@3']:+.1%} · "
            f"Hit@5 {d['hit@5']:+.1%} · MRR {d['mrr']:+.3f}",
            "",
            f"**Gold rank changes (on vs off):** {rc['up']} up · {rc['down']} down · "
            f"{rc['unchanged']} unchanged.",
            "",
            "| Tier (recency ON) | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |",
            "|---|---|---|---|---|---|",
        ]
        for tier, summary in corpus["recency_on"]["per_tier"].items():
            lines.append(_row(tier, summary))
        lines.append("")
    lines += [
        "> Caveat: title-derived queries favour lexical matching; if every gold "
        "ranks #1 in both runs the corpus may simply be easy for this query "
        "style — treat the delta and rank-change counts as the real signal.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    report = run()
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_render_md(report), encoding="utf-8")
    print(f"report : {REPORT_MD}")


if __name__ == "__main__":
    main()
