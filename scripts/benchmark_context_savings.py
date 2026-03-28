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
BENCHMARK_MD_RU = BENCHMARK_DIR / "latest.ru.md"
BENCHMARK_MD_UK = BENCHMARK_DIR / "latest.uk.md"
BENCHMARK_SVG_EN = BENCHMARK_DIR / "summary-en.svg"
BENCHMARK_SVG_RU = BENCHMARK_DIR / "summary-ru.svg"
BENCHMARK_SVG_UK = BENCHMARK_DIR / "summary-uk.svg"
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


def render_markdown(report: dict[str, Any], *, language: str) -> str:
    summary = report["summary"]
    copy = {
        "en": {
            "title": "# Benchmark Results",
            "generated_at": "Generated at",
            "corpus": "Corpus",
            "corpus_files_label": "Markdown files",
            "corpus_blocks_label": "indexed blocks",
            "full_index": "Full index",
            "idle_incremental": "Idle incremental",
            "image": "![Benchmark summary](summary-en.svg)",
            "at_a_glance": "## At a Glance",
            "metric": "Metric",
            "result": "Result",
            "meaning": "Why it matters",
            "what_it_means": "## What These Results Mean",
            "summary_points": [
                "The compact retrieval path is much smaller than naive full-file opening.",
                "Even after hydrating the top hit, the guided path still saves a lot of context.",
                "More of the model's context window stays available for reasoning instead of repeated reading.",
            ],
            "aggregate": "## Aggregate Savings",
            "strategy": "Strategy",
            "avg_bytes": "Average byte savings",
            "median_bytes": "Median byte savings",
            "avg_words": "Average word savings",
            "query_breakdown": "## Query Breakdown",
            "query": "Query",
            "top_hit": "Top hit",
            "full_files_bytes": "Full files bytes",
            "search_bytes": "Search bytes",
            "guided_bytes": "Search+hydrate bytes",
            "search_savings": "Search savings",
            "guided_savings": "Guided savings",
            "method": "## Method",
            "method_rows": [
                ("Baseline without MCP", "Open the full source text of every unique Markdown file represented in the top-5 project search hits"),
                ("Compact MCP path", "Use the `semantic_search` response only"),
                ("Guided MCP path", "Use `semantic_search` and then `hydrate` only for the top Markdown hit"),
            ],
            "method_note": "Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.",
            "at_a_glance_rows": [
                ("Corpus", lambda r: f"{r['corpus']['indexed_files']} files · {r['corpus']['block_count']} blocks", "This is measured on the real repository corpus"),
                ("Full index", lambda r: f"{round(r['indexing']['full_index_ms'] / 1000, 2)} s", "First-time indexing is short"),
                ("Idle incremental", lambda r: f"{round(r['indexing']['idle_incremental_ms'] / 1000, 2)} s", "Re-indexing after small changes is light"),
                ("Avg `semantic_search`", lambda r: f"{r['summary']['average_search_latency_ms']} ms", "Fast enough to use as the default retrieval path"),
                ("Avg `hydrate`", lambda r: f"{r['summary']['average_hydrate_latency_ms']} ms", "Opening more context stays cheap"),
                ("Search-only byte savings", lambda r: f"{r['summary']['average_semantic_search_only_bytes_saved_percent']}%", "Much less text is sent to the model"),
                ("Search + hydrate byte savings", lambda r: f"{r['summary']['average_semantic_search_plus_hydrate_bytes_saved_percent']}%", "Even the guided path stays clearly smaller than opening full files"),
            ],
        },
        "uk": {
            "title": "# Результати Бенчмарку",
            "generated_at": "Згенеровано",
            "corpus": "Корпус",
            "corpus_files_label": "Markdown-файлів",
            "corpus_blocks_label": "індексованих блоків",
            "full_index": "Повна індексація",
            "idle_incremental": "Порожній incremental",
            "image": "![Знімок benchmark](summary-uk.svg)",
            "at_a_glance": "## Коротко По Суті",
            "metric": "Метрика",
            "result": "Результат",
            "meaning": "Що це означає",
            "what_it_means": "## Що Означають Ці Результати",
            "summary_points": [
                "Компактний retrieval-потік набагато менший за наївне відкриття повних файлів.",
                "Навіть після hydration найкращого hit-а керований шлях усе одно добре економить контекст.",
                "Більше контекстного бюджету лишається на міркування, а не на повторне читання.",
            ],
            "aggregate": "## Сумарна Економія",
            "strategy": "Стратегія",
            "avg_bytes": "Середня економія байтів",
            "median_bytes": "Медіанна економія байтів",
            "avg_words": "Середня економія слів",
            "query_breakdown": "## Розбір По Запитах",
            "query": "Запит",
            "top_hit": "Топовий hit",
            "full_files_bytes": "Байти повних файлів",
            "search_bytes": "Байти пошуку",
            "guided_bytes": "Байти пошук+hydrate",
            "search_savings": "Економія пошуку",
            "guided_savings": "Економія керованого шляху",
            "method": "## Метод",
            "method_rows": [
                ("Базовий сценарій без MCP", "Відкрити повний текст кожного унікального Markdown-файлу, який представлений у топ-5 project search hit-ах"),
                ("Компактний MCP-шлях", "Використати тільки відповідь `semantic_search`"),
                ("Керований MCP-шлях", "Використати `semantic_search`, а потім `hydrate` тільки для топового Markdown hit-а"),
            ],
            "method_note": "Економія рахується відносно базового сценарію за реальними UTF-8 byte count і word count з цього репозиторію.",
            "at_a_glance_rows": [
                ("Корпус", lambda r: f"{r['corpus']['indexed_files']} файлів · {r['corpus']['block_count']} блоків", "Це вимірювання на реальному корпусі цього репозиторію"),
                ("Повна індексація", lambda r: f"{round(r['indexing']['full_index_ms'] / 1000, 2)} с", "Перша індексація лишається короткою"),
                ("Порожній incremental", lambda r: f"{round(r['indexing']['idle_incremental_ms'] / 1000, 2)} с", "Переіндексація після малих змін лишається легкою"),
                ("Середній `semantic_search`", lambda r: f"{r['summary']['average_search_latency_ms']} мс", "Цього достатньо для дефолтного retrieval-шляху"),
                ("Середній `hydrate`", lambda r: f"{r['summary']['average_hydrate_latency_ms']} мс", "Відкривати більше контексту все ще дешево"),
                ("Економія байтів, лише пошук", lambda r: f"{r['summary']['average_semantic_search_only_bytes_saved_percent']}%", "До моделі передається значно менше тексту"),
                ("Економія байтів, пошук + hydrate", lambda r: f"{r['summary']['average_semantic_search_plus_hydrate_bytes_saved_percent']}%", "Навіть керований шлях помітно менший за відкриття повних файлів"),
            ],
        },
        "ru": {
            "title": "# Результаты Бенчмарка",
            "generated_at": "Сгенерировано",
            "corpus": "Корпус",
            "corpus_files_label": "Markdown-файлов",
            "corpus_blocks_label": "индексированных блоков",
            "full_index": "Полная индексация",
            "idle_incremental": "Пустой incremental",
            "image": "![Снимок benchmark](summary-ru.svg)",
            "at_a_glance": "## Коротко По Сути",
            "metric": "Метрика",
            "result": "Результат",
            "meaning": "Что это означает",
            "what_it_means": "## Что Означают Эти Результаты",
            "summary_points": [
                "Компактный retrieval-путь намного меньше наивного открытия полных файлов.",
                "Даже после hydration лучшего hit-а управляемый путь всё равно хорошо экономит контекст.",
                "Больше контекстного бюджета остаётся на рассуждение, а не на повторное чтение.",
            ],
            "aggregate": "## Суммарная Экономия",
            "strategy": "Стратегия",
            "avg_bytes": "Средняя экономия байтов",
            "median_bytes": "Медианная экономия байтов",
            "avg_words": "Средняя экономия слов",
            "query_breakdown": "## Разбор По Запросам",
            "query": "Запрос",
            "top_hit": "Топовый hit",
            "full_files_bytes": "Байты полных файлов",
            "search_bytes": "Байты поиска",
            "guided_bytes": "Байты поиск+hydrate",
            "search_savings": "Экономия поиска",
            "guided_savings": "Экономия управляемого пути",
            "method": "## Метод",
            "method_rows": [
                ("Базовый сценарий без MCP", "Открыть полный текст каждого уникального Markdown-файла, который представлен в топ-5 project search hit-ах"),
                ("Компактный MCP-путь", "Использовать только ответ `semantic_search`"),
                ("Управляемый MCP-путь", "Использовать `semantic_search`, а затем `hydrate` только для топового Markdown hit-а"),
            ],
            "method_note": "Экономия считается относительно базового сценария по реальным UTF-8 byte count и word count из этого репозитория.",
            "at_a_glance_rows": [
                ("Корпус", lambda r: f"{r['corpus']['indexed_files']} файлов · {r['corpus']['block_count']} блоков", "Это измерение на реальном корпусе этого репозитория"),
                ("Полная индексация", lambda r: f"{round(r['indexing']['full_index_ms'] / 1000, 2)} с", "Первичная индексация остаётся короткой"),
                ("Пустой incremental", lambda r: f"{round(r['indexing']['idle_incremental_ms'] / 1000, 2)} с", "Переиндексация после малых изменений остаётся лёгкой"),
                ("Средний `semantic_search`", lambda r: f"{r['summary']['average_search_latency_ms']} мс", "Этого достаточно для стандартного retrieval-пути"),
                ("Средний `hydrate`", lambda r: f"{r['summary']['average_hydrate_latency_ms']} мс", "Открывать больше контекста всё ещё дёшево"),
                ("Экономия байтов, только поиск", lambda r: f"{r['summary']['average_semantic_search_only_bytes_saved_percent']}%", "В модель передаётся заметно меньше текста"),
                ("Экономия байтов, поиск + hydrate", lambda r: f"{r['summary']['average_semantic_search_plus_hydrate_bytes_saved_percent']}%", "Даже управляемый путь заметно меньше открытия полных файлов"),
            ],
        },
    }[language]

    lines = [
        copy["title"],
        "",
        f"- {copy['generated_at']}: `{report['generated_at']}`",
        f"- {copy['corpus']}: `{report['corpus']['indexed_files']}` {copy['corpus_files_label']}, `{report['corpus']['block_count']}` {copy['corpus_blocks_label']}",
        f"- {copy['full_index']}: `{report['indexing']['full_index_ms']}` ms",
        f"- {copy['idle_incremental']}: `{report['indexing']['idle_incremental_ms']}` ms",
        "",
        copy["image"],
        "",
        copy["at_a_glance"],
        "",
        f"| {copy['metric']} | {copy['result']} | {copy['meaning']} |",
        "|---|---:|---|",
    ]

    for label, value_fn, meaning in copy["at_a_glance_rows"]:
        lines.append(f"| {label} | {value_fn(report)} | {meaning} |")

    lines.extend(
        [
            "",
            copy["what_it_means"],
            "",
        ]
    )
    for point in copy["summary_points"]:
        lines.append(f"- {point}")

    lines.extend(
        [
            "",
            copy["aggregate"],
            "",
            f"| {copy['strategy']} | {copy['avg_bytes']} | {copy['median_bytes']} | {copy['avg_words']} |",
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
            copy["query_breakdown"],
            "",
            f"| {copy['query']} | {copy['top_hit']} | {copy['full_files_bytes']} | {copy['search_bytes']} | {copy['guided_bytes']} | {copy['search_savings']} | {copy['guided_savings']} |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )

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
            copy["method"],
            "",
            f"| {copy['strategy']} | {copy['meaning']} |",
            "|---|---|",
        ]
    )
    for label, description in copy["method_rows"]:
        lines.append(f"| {label} | {description} |")

    lines.extend(["", copy["method_note"]])
    return "\n".join(lines) + "\n"


def render_svg(report: dict[str, Any], *, language: str) -> str:
    summary = report["summary"]
    corpus = report["corpus"]
    indexing = report["indexing"]

    copy = {
        "en": {
            "title": "Benchmark Snapshot",
            "desc": "Real repository measurements with readable benchmark cards and context savings bars.",
            "subtitle": "Real repository measurements",
            "corpus": "Corpus",
            "index": "Full index",
            "search": "Avg search",
            "hydrate": "Avg hydrate",
            "compact": "Search only",
            "guided": "Search + hydrate",
            "bytes_saved": "average byte savings",
            "word_saved": "average word savings",
            "files_value": f"{corpus['indexed_files']} files",
            "files_meta": f"{corpus['block_count']} indexed blocks",
            "index_value": f"{round(indexing['full_index_ms'] / 1000, 2)} s",
            "index_meta": "first full index run",
            "search_value": f"{summary['average_search_latency_ms']} ms",
            "search_meta": "semantic_search latency",
            "hydrate_value": f"{summary['average_hydrate_latency_ms']} ms",
            "hydrate_meta": "hydrate latency",
        },
        "ru": {
            "title": "Снимок benchmark",
            "desc": "Реальные измерения репозитория с читаемыми карточками и полосами экономии контекста.",
            "subtitle": "Реальные измерения репозитория",
            "corpus": "Корпус",
            "index": "Полная индексация",
            "search": "Средний поиск",
            "hydrate": "Средний hydrate",
            "compact": "Только поиск",
            "guided": "Поиск + hydrate",
            "bytes_saved": "средняя экономия по байтам",
            "word_saved": "средняя экономия по словам",
            "files_value": f"{corpus['indexed_files']} файлов",
            "files_meta": f"{corpus['block_count']} индексированных блоков",
            "index_value": f"{round(indexing['full_index_ms'] / 1000, 2)} с",
            "index_meta": "первый полный проход",
            "search_value": f"{summary['average_search_latency_ms']} мс",
            "search_meta": "задержка semantic_search",
            "hydrate_value": f"{summary['average_hydrate_latency_ms']} мс",
            "hydrate_meta": "задержка hydrate",
        },
        "uk": {
            "title": "Знімок benchmark",
            "desc": "Реальні вимірювання репозиторію з читабельними картками та смугами економії контексту.",
            "subtitle": "Реальні вимірювання репозиторію",
            "corpus": "Корпус",
            "index": "Повна індексація",
            "search": "Середній пошук",
            "hydrate": "Середній hydrate",
            "compact": "Лише пошук",
            "guided": "Пошук + hydrate",
            "bytes_saved": "середня економія по байтах",
            "word_saved": "середня економія по словах",
            "files_value": f"{corpus['indexed_files']} файлів",
            "files_meta": f"{corpus['block_count']} індексованих блоків",
            "index_value": f"{round(indexing['full_index_ms'] / 1000, 2)} с",
            "index_meta": "перший повний прохід",
            "search_value": f"{summary['average_search_latency_ms']} мс",
            "search_meta": "затримка semantic_search",
            "hydrate_value": f"{summary['average_hydrate_latency_ms']} мс",
            "hydrate_meta": "затримка hydrate",
        },
    }[language]

    compact_width = round(summary["average_semantic_search_only_bytes_saved_percent"] * 9.8, 1)
    guided_width = round(summary["average_semantic_search_plus_hydrate_bytes_saved_percent"] * 9.8, 1)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="820" viewBox="0 0 1400 820" role="img" aria-labelledby="title desc">
  <title id="title">{copy['title']}</title>
  <desc id="desc">{copy['desc']}</desc>
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#07111f"/>
      <stop offset="55%" stop-color="#0b2840"/>
      <stop offset="100%" stop-color="#0d4d63"/>
    </linearGradient>
    <linearGradient id="card" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#173550"/>
      <stop offset="100%" stop-color="#20415c"/>
    </linearGradient>
    <linearGradient id="bar1" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#53d6ff"/>
      <stop offset="100%" stop-color="#85ffb3"/>
    </linearGradient>
    <linearGradient id="bar2" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#55b8ff"/>
      <stop offset="100%" stop-color="#4fe4e4"/>
    </linearGradient>
    <style>
      .title {{ font: 700 56px 'Segoe UI', Arial, sans-serif; fill: #f7fbff; }}
      .subtitle {{ font: 500 24px 'Segoe UI', Arial, sans-serif; fill: #a4ebff; }}
      .kpiLabel {{ font: 600 22px 'Segoe UI', Arial, sans-serif; fill: #9fe7ff; }}
      .kpiValue {{ font: 700 48px 'Segoe UI', Arial, sans-serif; fill: #f8fcff; }}
      .kpiMeta {{ font: 500 20px 'Segoe UI', Arial, sans-serif; fill: #cfe3f1; }}
      .barLabel {{ font: 700 30px 'Segoe UI', Arial, sans-serif; fill: #f4fbff; }}
      .barMeta {{ font: 500 20px 'Segoe UI', Arial, sans-serif; fill: #cbe2f0; }}
      .percent {{ font: 700 34px 'Segoe UI', Arial, sans-serif; fill: #faffff; }}
    </style>
  </defs>

  <rect width="1400" height="820" rx="32" ry="32" fill="url(#bg)"/>

  <text x="70" y="90" class="title">{copy['title']}</text>
  <text x="70" y="126" class="subtitle">{copy['subtitle']}</text>

  <rect x="70" y="170" rx="24" ry="24" width="610" height="126" fill="url(#card)"/>
  <rect x="720" y="170" rx="24" ry="24" width="610" height="126" fill="url(#card)"/>
  <rect x="70" y="316" rx="24" ry="24" width="610" height="126" fill="url(#card)"/>
  <rect x="720" y="316" rx="24" ry="24" width="610" height="126" fill="url(#card)"/>

  <text x="96" y="215" class="kpiLabel">{copy['corpus']}</text>
  <text x="96" y="260" class="kpiValue">{copy['files_value']}</text>
  <text x="96" y="286" class="kpiMeta">{copy['files_meta']}</text>

  <text x="746" y="215" class="kpiLabel">{copy['index']}</text>
  <text x="746" y="260" class="kpiValue">{copy['index_value']}</text>
  <text x="746" y="286" class="kpiMeta">{copy['index_meta']}</text>

  <text x="96" y="361" class="kpiLabel">{copy['search']}</text>
  <text x="96" y="406" class="kpiValue">{copy['search_value']}</text>
  <text x="96" y="432" class="kpiMeta">{copy['search_meta']}</text>

  <text x="746" y="361" class="kpiLabel">{copy['hydrate']}</text>
  <text x="746" y="406" class="kpiValue">{copy['hydrate_value']}</text>
  <text x="746" y="432" class="kpiMeta">{copy['hydrate_meta']}</text>

  <text x="70" y="500" class="barLabel">{copy['compact']}</text>
  <text x="70" y="535" class="barMeta">{copy['bytes_saved']}</text>
  <rect x="70" y="560" rx="24" ry="24" width="980" height="52" fill="#123248"/>
  <rect x="70" y="560" rx="24" ry="24" width="{compact_width}" height="52" fill="url(#bar1)"/>
  <text x="1230" y="597" class="percent" text-anchor="end">{summary['average_semantic_search_only_bytes_saved_percent']}%</text>
  <text x="70" y="632" class="barMeta">{copy['word_saved']}: {summary['average_semantic_search_only_words_saved_percent']}%</text>

  <text x="70" y="684" class="barLabel">{copy['guided']}</text>
  <text x="70" y="718" class="barMeta">{copy['bytes_saved']}</text>
  <rect x="70" y="740" rx="24" ry="24" width="980" height="44" fill="#123248"/>
  <rect x="70" y="740" rx="24" ry="24" width="{guided_width}" height="44" fill="url(#bar2)"/>
  <text x="1230" y="772" class="percent" text-anchor="end">{summary['average_semantic_search_plus_hydrate_bytes_saved_percent']}%</text>
  <text x="70" y="806" class="barMeta">{copy['word_saved']}: {summary['average_semantic_search_plus_hydrate_words_saved_percent']}%</text>
</svg>
"""


def main() -> int:
    report = run_benchmark()
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    BENCHMARK_JSON.write_text(_json_text(report) + "\n", encoding="utf-8")
    BENCHMARK_MD.write_text(render_markdown(report, language="en"), encoding="utf-8")
    BENCHMARK_MD_RU.write_text(render_markdown(report, language="ru"), encoding="utf-8")
    BENCHMARK_MD_UK.write_text(render_markdown(report, language="uk"), encoding="utf-8")
    BENCHMARK_SVG_EN.write_text(render_svg(report, language="en"), encoding="utf-8")
    BENCHMARK_SVG_RU.write_text(render_svg(report, language="ru"), encoding="utf-8")
    BENCHMARK_SVG_UK.write_text(render_svg(report, language="uk"), encoding="utf-8")
    print(f"Wrote {BENCHMARK_JSON}")
    print(f"Wrote {BENCHMARK_MD}")
    print(f"Wrote {BENCHMARK_MD_RU}")
    print(f"Wrote {BENCHMARK_MD_UK}")
    print(f"Wrote {BENCHMARK_SVG_EN}")
    print(f"Wrote {BENCHMARK_SVG_RU}")
    print(f"Wrote {BENCHMARK_SVG_UK}")
    print(_json_text(report["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
