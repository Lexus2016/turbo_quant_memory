from __future__ import annotations

import math
import sys
import types

import pytest

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


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def test_backend_parity_real_models() -> None:
    """Guard against silent embedding drift between the two backends.

    fastembed ships its own ONNX conversions, and a bad conversion is a known
    failure mode (mpnet via fastembed used CLS instead of mean pooling and
    silently tanked retrieval). A correct conversion of our model stays at
    cosine ~0.999+ against the PyTorch reference; a pooling/model mismatch
    drops it far below 0.99. Runs only when the [onnx] extra is installed.
    """
    pytest.importorskip("fastembed")

    phrases = [
        "The daemon acquires the lockfile before serving requests.",
        "Виправлено помилку зі станом гонитви у черзі підтверджень.",
        "La configuración del servidor se guarda en un archivo JSON.",
        "缓存索引在重启后仍然有效。",
    ]
    reference = ri._load_default_embedder().encode(phrases)
    candidate = ri._load_fastembed_embedder().encode(phrases)

    for phrase, ref_vec, cand_vec in zip(phrases, reference, candidate, strict=True):
        similarity = _cosine([float(v) for v in ref_vec], cand_vec)
        assert similarity >= 0.99, (
            f"Backend drift on {phrase!r}: cosine {similarity:.4f} < 0.99 — "
            "fastembed's ONNX conversion no longer matches the PyTorch reference"
        )
