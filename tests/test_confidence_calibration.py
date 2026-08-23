"""Calibration regression tests for confidence-state labels.

Consensus (codex/claude/grok, 2026-08-23): assert calibration BANDS on labeled
query classes instead of raw scores — raw scores drift with corpus growth and
embedder changes, but a recalibration must show its effect on these buckets.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_semantic_search import KeywordEmbedder
from turbo_memory_mcp.retrieval_index import (
    CONFIDENCE_HIGH_SCORE,
    CONFIDENCE_MEDIUM_SCORE,
    VECTOR_GATE_THRESHOLD,
)
from turbo_memory_mcp.server import build_runtime_context, semantic_search_impl
from turbo_memory_mcp.store import sha256_text


@pytest.fixture(autouse=True)
def _fake_embedder() -> None:
    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=KeywordEmbedder()):
        yield


def _test_env(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "calibration-alpha",
        "TQMEMORY_PROJECT_NAME": "Calibration Alpha",
    }


def _seed_block(tmp_path: Path, env: dict[str, str], *, block_id: str, text: str) -> None:
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "docs-root-hash",
        }
    )
    store.write_markdown_block(
        {
            "block_id": block_id,
            "root_id": "docs-root",
            "source_path": f"docs/{block_id}.md",
            "heading_path": ["Docs"],
            "chunk_index": 0,
            "content_raw": text,
            "block_checksum": sha256_text(text),
            "source_checksum": f"checksum-{block_id}",
        }
    )


CORPUS: tuple[tuple[str, str], ...] = (
    ("auth-rotation", "Auth refresh rotation keeps session cache stable for login flows."),
    ("kubernetes-runbook", "Kubernetes helm chart rollout and pod autoscaling runbook."),
    ("secrets-vault", "Secrets vault encryption uses AES GCM with Argon2id key derivation."),
    ("daemon-proxy", "Daemon proxy forwards requests to the primary server over IPC."),
    ("markdown-indexing", "Markdown indexing chunks documents into retrieval blocks by heading."),
)

TARGETED_QUERIES: tuple[tuple[str, str], ...] = (
    ("auth-rotation", "auth refresh rotation session cache"),
    ("kubernetes-runbook", "kubernetes helm chart rollout"),
    ("secrets-vault", "secrets vault encryption argon2id"),
    ("daemon-proxy", "daemon proxy primary server ipc"),
    ("markdown-indexing", "markdown indexing retrieval blocks heading"),
)

MULTI_TOPIC_QUERIES: tuple[str, ...] = (
    # Vague, broad queries of the kind agents really send. Every term exists in
    # the corpus vocabulary, but no single note is the unique target.
    "encryption indexing proxy rollout",
    "session chart daemon vault",
)


def test_calibration_constants_are_decoupled() -> None:
    """Label thresholds must never alias the fusion gate again."""
    assert CONFIDENCE_HIGH_SCORE == 0.72
    assert CONFIDENCE_MEDIUM_SCORE == 0.52
    assert VECTOR_GATE_THRESHOLD == 0.82
    assert CONFIDENCE_HIGH_SCORE < VECTOR_GATE_THRESHOLD


def test_targeted_queries_majority_high_band(tmp_path: Path) -> None:
    """Unique-target queries must land 'high' for most cases.

    Band assertion (not exact scores): the OLD 0.82 threshold equalled the
    measured median top1, so half of precise queries missed 'high'. The 0.72
    label must restore a clear majority without promoting weak matches.
    """
    env = _test_env(tmp_path)
    for block_id, text in CORPUS:
        _seed_block(tmp_path, env, block_id=block_id, text=text)

    high_count = 0
    for expected_id, query in TARGETED_QUERIES:
        payload = semantic_search_impl(query, scope="project", limit=3, environ=env)
        items = payload["items"]
        top = items[0]
        assert top.get("block_id") == expected_id or top.get("item_id") == expected_id
        if top["confidence_state"] == "high":
            high_count += 1

    assert high_count >= 4, f"expected majority-high calibration, got {high_count}/{len(TARGETED_QUERIES)}"


def test_multi_topic_hits_never_carry_per_item_warning(tmp_path: Path) -> None:
    """Broad multi-topic queries get ONE response-level notice, never per-item.

    Regression guard for the alert-fatigue defect: relevant hits at mid scores
    each carried 'Low-confidence retrieval...' and agents learned to ignore it.
    """
    env = _test_env(tmp_path)
    for block_id, text in CORPUS:
        _seed_block(tmp_path, env, block_id=block_id, text=text)

    for query in MULTI_TOPIC_QUERIES:
        payload = semantic_search_impl(query, scope="project", limit=5, environ=env)
        items = payload["items"]
        assert items, f"expected results for {query!r}"
        for item in items:
            assert "warning" not in item, (
                f"per-item warning leaked into multi-topic result {item['item_id']}"
            )
        top_confidence = float(items[0]["confidence"])
        if top_confidence < CONFIDENCE_MEDIUM_SCORE:
            assert "refine" in str(payload.get("warning", "")).lower()


def test_weak_query_gets_single_response_level_notice(tmp_path: Path) -> None:
    """When even the best hit is below MEDIUM, exactly one actionable notice."""
    env = _test_env(tmp_path)
    _seed_block(
        tmp_path,
        env,
        block_id="auth-rotation",
        text="Auth refresh rotation keeps session cache stable for login flows.",
    )

    payload = semantic_search_impl("quantum entanglement budget forecasting", scope="project", limit=3, environ=env)
    items = payload["items"]
    for item in items:
        assert "warning" not in item
    if items and float(items[0]["confidence"]) < CONFIDENCE_MEDIUM_SCORE:
        assert payload["warning"] == "Results may be broad; refine the query or hydrate if needed."
