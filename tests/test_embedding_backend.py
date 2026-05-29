from __future__ import annotations

import sys
import types

import turbo_memory_mcp.retrieval_index as ri


def test_backend_dispatch_follows_env(monkeypatch) -> None:
    sentinel_default = object()
    sentinel_fastembed = object()
    monkeypatch.setattr(ri, "_load_default_embedder", lambda: sentinel_default)
    monkeypatch.setattr(ri, "_load_fastembed_embedder", lambda: sentinel_fastembed)

    monkeypatch.delenv("TQMEMORY_EMBEDDING_BACKEND", raising=False)
    assert ri.build_default_embedder() is sentinel_default  # default = sentence-transformers

    monkeypatch.setenv("TQMEMORY_EMBEDDING_BACKEND", "fastembed")
    assert ri.build_default_embedder() is sentinel_fastembed

    monkeypatch.setenv("TQMEMORY_EMBEDDING_BACKEND", "Sentence-Transformers")  # case-insensitive
    assert ri.build_default_embedder() is sentinel_default


def test_fastembed_adapter_shape(monkeypatch) -> None:
    class _FakeTextEmbedding:
        def __init__(self, name: str) -> None:
            self.name = name

        def embed(self, texts):
            # fastembed yields one numpy-like vector per text
            return ([0.1, 0.2, 0.3] for _ in texts)

    fake_module = types.ModuleType("fastembed")
    fake_module.TextEmbedding = _FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    embedder = ri._FastEmbedEmbedder("any/model")
    out = embedder.encode(["alpha", "beta"])

    assert len(out) == 2
    assert all(len(vec) == 3 for vec in out)
    assert all(isinstance(v, float) for vec in out for v in vec)
