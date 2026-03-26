from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from turbo_memory_mcp.server import hydrate_impl, index_paths_impl, semantic_search_impl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_ROOT / "benchmarks"
BENCHMARK_JSON = BENCHMARK_DIR / "latest.json"
BENCHMARK_MD = BENCHMARK_DIR / "latest.md"
QUERY_SET = [
    "namespace model project global hybrid",
    "hydrate bounded neighborhood related mode",
    "current project resolution git remote overrides",
    "storage stats freshness index status",
    "Claude Code Codex Cursor OpenCode integrations",
    "remember note decision lesson handoff pattern",
]


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _text_metrics(text: str) -> dict[str, int]:
    stripped = text.strip()
    words = len(stripped.split()) if stripped else 0
    lines = text.count("\n") + (1 if text else 0)
    return {
        "bytes": len(text.encode("utf-8")),
        "words": words,
        "lines": lines,
    }


def _payload_metrics(payload: Any) -> dict[str, int]:
    return _text_metrics(_json_text(payload))


def _full_file_metrics(paths: list[Path]) -> dict[str, Any]:
    total_bytes = 0
    total_words = 0
    total_lines = 0
    files: list[dict[str, Any]] = []

    for path in paths:
        text = path.read_text(encoding="utf-8")
        metrics = _text_metrics(text)
        total_bytes += metrics["bytes"]
        total_words += metrics["words"]
        total_lines += metrics["lines"]
        files.append(
            {
                "path": str(path.relative_to(PROJECT_ROOT)),
                **metrics,
            }
        )

    return {
        "file_count": len(paths),
        "bytes": total_bytes,
        "words": total_words,
        "lines": total_lines,
        "files": files,
    }


def _savings_percent(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round((1 - (candidate / baseline)) * 100, 2)


def _relative_source_path(source_path: str) -> Path:
    resolved = Path(source_path)
    if resolved.is_absolute():
        return resolved
    return (PROJECT_ROOT / resolved).resolve()


def run_benchmark() -> dict[str, Any]:
    with TemporaryDirectory(prefix="tqmemory-bench-") as temp_dir:
        env = {
            **os.environ,
            "TQMEMORY_HOME": str((Path(temp_dir) / "memory-home").resolve()),
            "TQMEMORY_PROJECT_ROOT": str(PROJECT_ROOT),
        }

        full_index_started = perf_counter()
        full_index = index_paths_impl(paths=["."], mode="full", cwd=PROJECT_ROOT, environ=env)
        full_index_ms = round((perf_counter() - full_index_started) * 1000, 2)

        incremental_started = perf_counter()
        idle_incremental = index_paths_impl(mode="incremental", cwd=PROJECT_ROOT, environ=env)
        idle_incremental_ms = round((perf_counter() - incremental_started) * 1000, 2)

        query_reports: list[dict[str, Any]] = []
        compact_savings_bytes: list[float] = []
        guided_savings_bytes: list[float] = []
        compact_savings_words: list[float] = []
        guided_savings_words: list[float] = []
        search_latencies: list[float] = []
        hydrate_latencies: list[float] = []

        for query in QUERY_SET:
            search_started = perf_counter()
            search_payload = semantic_search_impl(
                query,
                scope="project",
                limit=5,
                cwd=PROJECT_ROOT,
                environ=env,
            )
            search_ms = round((perf_counter() - search_started) * 1000, 2)
            search_latencies.append(search_ms)

            markdown_items = [item for item in search_payload["items"] if item["source_kind"] == "markdown"]
            if not markdown_items:
                raise AssertionError(f"Benchmark query returned no markdown hits: {query}")

            top_item = markdown_items[0]
            hydrate_started = perf_counter()
            hydrate_payload = hydrate_impl(
                top_item["item_id"],
                scope=top_item["scope"],
                mode="default",
                cwd=PROJECT_ROOT,
                environ=env,
            )
            hydrate_ms = round((perf_counter() - hydrate_started) * 1000, 2)
            hydrate_latencies.append(hydrate_ms)

            unique_source_paths = sorted(
                {
                    _relative_source_path(item["source_path"])
                    for item in markdown_items
                }
            )
            baseline_metrics = _full_file_metrics(unique_source_paths)
            compact_metrics = _payload_metrics(search_payload)
            guided_metrics = _payload_metrics(
                {
                    "semantic_search": search_payload,
                    "hydrate_top_hit": hydrate_payload,
                }
            )

            compact_bytes_saved = _savings_percent(baseline_metrics["bytes"], compact_metrics["bytes"])
            guided_bytes_saved = _savings_percent(baseline_metrics["bytes"], guided_metrics["bytes"])
            compact_words_saved = _savings_percent(baseline_metrics["words"], compact_metrics["words"])
            guided_words_saved = _savings_percent(baseline_metrics["words"], guided_metrics["words"])

            compact_savings_bytes.append(compact_bytes_saved)
            guided_savings_bytes.append(guided_bytes_saved)
            compact_savings_words.append(compact_words_saved)
            guided_savings_words.append(guided_words_saved)

            query_reports.append(
                {
                    "query": query,
                    "search_latency_ms": search_ms,
                    "hydrate_latency_ms": hydrate_ms,
                    "top_hit": {
                        "title": top_item["title"],
                        "source_path": top_item["source_path"],
                        "score": top_item["score"],
                        "confidence_state": top_item["confidence_state"],
                    },
                    "naive_full_files": baseline_metrics,
                    "semantic_search_payload": compact_metrics,
                    "semantic_search_plus_hydrate": guided_metrics,
                    "savings": {
                        "semantic_search_only": {
                            "bytes_percent": compact_bytes_saved,
                            "words_percent": compact_words_saved,
                        },
                        "semantic_search_plus_hydrate": {
                            "bytes_percent": guided_bytes_saved,
                            "words_percent": guided_words_saved,
                        },
                    },
                }
            )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "corpus": {
            "indexed_files": full_index["indexed_files"],
            "changed_files_on_full": full_index["changed_files"],
            "block_count": full_index["block_count"],
        },
        "indexing": {
            "full_index_ms": full_index_ms,
            "idle_incremental_ms": idle_incremental_ms,
            "idle_incremental_payload": idle_incremental,
        },
        "baseline_definition": {
            "without_mcp": "Open the full source text of every unique Markdown file represented in the top-5 project search hits.",
            "with_mcp_compact": "Use only the semantic_search JSON response.",
            "with_mcp_guided": "Use semantic_search JSON plus hydrate on the top Markdown hit.",
        },
        "queries": query_reports,
        "summary": {
            "query_count": len(query_reports),
            "average_search_latency_ms": round(mean(search_latencies), 2),
            "median_search_latency_ms": round(median(search_latencies), 2),
            "average_hydrate_latency_ms": round(mean(hydrate_latencies), 2),
            "median_hydrate_latency_ms": round(median(hydrate_latencies), 2),
            "average_semantic_search_only_bytes_saved_percent": round(mean(compact_savings_bytes), 2),
            "median_semantic_search_only_bytes_saved_percent": round(median(compact_savings_bytes), 2),
            "average_semantic_search_only_words_saved_percent": round(mean(compact_savings_words), 2),
            "average_semantic_search_plus_hydrate_bytes_saved_percent": round(mean(guided_savings_bytes), 2),
            "median_semantic_search_plus_hydrate_bytes_saved_percent": round(median(guided_savings_bytes), 2),
            "average_semantic_search_plus_hydrate_words_saved_percent": round(mean(guided_savings_words), 2),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Benchmark Results",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Corpus: `{report['corpus']['indexed_files']}` Markdown files, `{report['corpus']['block_count']}` indexed blocks",
        f"- Full index: `{report['indexing']['full_index_ms']}` ms",
        f"- Idle incremental: `{report['indexing']['idle_incremental_ms']}` ms",
        "",
        "## Aggregate Savings",
        "",
        "| Strategy | Average byte savings | Median byte savings | Average word savings |",
        "|---|---:|---:|---:|",
        (
            f"| `semantic_search` only | "
            f"{summary['average_semantic_search_only_bytes_saved_percent']}% | "
            f"{summary['median_semantic_search_only_bytes_saved_percent']}% | "
            f"{summary['average_semantic_search_only_words_saved_percent']}% |"
        ),
        (
            f"| `semantic_search` + `hydrate(top1)` | "
            f"{summary['average_semantic_search_plus_hydrate_bytes_saved_percent']}% | "
            f"{summary['median_semantic_search_plus_hydrate_bytes_saved_percent']}% | "
            f"{summary['average_semantic_search_plus_hydrate_words_saved_percent']}% |"
        ),
        "",
        "## Query Breakdown",
        "",
        "| Query | Top hit | Full files bytes | Search bytes | Search+hydrate bytes | Search savings | Guided savings |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    for query_report in report["queries"]:
        lines.append(
            (
                f"| `{query_report['query']}` | "
                f"`{query_report['top_hit']['title']}` | "
                f"{query_report['naive_full_files']['bytes']} | "
                f"{query_report['semantic_search_payload']['bytes']} | "
                f"{query_report['semantic_search_plus_hydrate']['bytes']} | "
                f"{query_report['savings']['semantic_search_only']['bytes_percent']}% | "
                f"{query_report['savings']['semantic_search_plus_hydrate']['bytes_percent']}% |"
            )
        )

    lines.extend(
        [
            "",
            "## Method",
            "",
            "- Baseline without MCP: open the full source text of every unique Markdown file represented in the top-5 project search hits.",
            "- Compact MCP path: use the `semantic_search` response only.",
            "- Guided MCP path: use `semantic_search` and then `hydrate` only for the top Markdown hit.",
            "- Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    report = run_benchmark()
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    BENCHMARK_JSON.write_text(_json_text(report) + "\n", encoding="utf-8")
    BENCHMARK_MD.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {BENCHMARK_JSON}")
    print(f"Wrote {BENCHMARK_MD}")
    print(_json_text(report["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
