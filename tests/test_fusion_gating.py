from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import turbo_memory_mcp.retrieval_index as ri
from turbo_memory_mcp.retrieval_index import RetrievalIndex, _rrf_merge
from turbo_memory_mcp.server import build_runtime_context, remember_note_impl


class KeywordEmbedder:
    KEYWORDS = ("auth", "refresh", "rotation", "token", "session", "cache")

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 384
            for index, keyword in enumerate(self.KEYWORDS):
                vector[index] = 1.0 if keyword in lowered else 0.0
            vectors.append(vector)
        return vectors


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
        "TQMEMORY_PROJECT_ID": "project-fusion",
        "TQMEMORY_PROJECT_NAME": "Fusion Test",
    }


def test_rrf_merge_down_weights_a_lane() -> None:
    vector_lane = [{"item_id": "v1"}, {"item_id": "v2"}, {"item_id": "v3"}]
    fts_lane = [{"item_id": "f1"}]  # FTS-only hit at rank 1

    equal = [row["item_id"] for row in _rrf_merge([vector_lane, fts_lane], k=60, limit=4)]
    # Under equal weights the rank-1 FTS-only hit outranks the rank-3 vector hit.
    assert equal.index("f1") < equal.index("v3")

    weighted = [
        row["item_id"]
        for row in _rrf_merge([vector_lane, fts_lane], k=60, limit=4, weights=[1.0, 0.05])
    ]
    # Down-weighting the FTS lane pushes the FTS-only hit below the vector hits.
    assert weighted.index("f1") > weighted.index("v3")


def test_gating_skips_fts_when_vector_is_confident(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    repo = tmp_path / "repo"
    remember_note_impl("Auth", "auth refresh rotation token", kind="lesson", cwd=repo, environ=env)
    _, store = build_runtime_context(cwd=repo, environ=env)
    index = RetrievalIndex(store)

    with patch.object(ri, "_safe_fts_search", wraps=ri._safe_fts_search) as fts_spy:
        # Query identical to the note -> dense top hit is exact -> gate -> no BM25.
        index.search("auth refresh rotation token", "project", limit=5)
        assert fts_spy.call_count == 0

        # Query with no shared keywords -> dense confidence low -> BM25 rescue runs.
        index.search("kubernetes deployment manifest", "project", limit=5)
        assert fts_spy.call_count == 1
