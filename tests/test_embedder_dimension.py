from __future__ import annotations

from unittest.mock import patch

from turbo_memory_mcp.retrieval_index import (
    DEFAULT_VECTOR_DIMENSIONS,
    _resolve_vector_dimensions,
    _table_schema,
)


class _Fake1024:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1024 for _ in texts]


class _BrokenEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")


def _vector_list_size(schema) -> int:
    return schema.field("vector").type.list_size


def test_dimension_follows_a_higher_dim_model() -> None:
    # Simulate a deployer switching to a 1024-dim model (e.g. BAAI/bge-m3) without
    # needing the real model: the schema must follow, not stay pinned at 384.
    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=_Fake1024()):
        assert _resolve_vector_dimensions() == 1024
        assert _vector_list_size(_table_schema()) == 1024


def test_dimension_falls_back_when_model_unprobeable() -> None:
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=_BrokenEmbedder()
    ):
        assert _resolve_vector_dimensions() == DEFAULT_VECTOR_DIMENSIONS
        assert _vector_list_size(_table_schema()) == DEFAULT_VECTOR_DIMENSIONS


def test_explicit_dimension_overrides_probe() -> None:
    assert _vector_list_size(_table_schema(dimensions=768)) == 768
