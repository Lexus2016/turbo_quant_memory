from __future__ import annotations

import math
import sys
import types

import pytest

import turbo_memory_mcp.retrieval_index as ri


def test_backend_dispatch_follows_env(monkeypatch) -> None:
    sentinel_torch = object()
    sentinel_fastembed = object()
    monkeypatch.setattr(ri, "_load_torch_embedder", lambda: sentinel_torch)
    monkeypatch.setattr(ri, "_load_fastembed_embedder", lambda: sentinel_fastembed)

    monkeypatch.delenv("TQMEMORY_EMBEDDING_BACKEND", raising=False)
    assert ri.build_default_embedder() is sentinel_fastembed  # default = fastembed/ONNX

    monkeypatch.setenv("TQMEMORY_EMBEDDING_BACKEND", "sentence-transformers")
    assert ri.build_default_embedder() is sentinel_torch

    monkeypatch.setenv("TQMEMORY_EMBEDDING_BACKEND", "Sentence-Transformers")  # case-insensitive
    assert ri.build_default_embedder() is sentinel_torch

    monkeypatch.setenv("TQMEMORY_EMBEDDING_BACKEND", "fastembed")
    assert ri.build_default_embedder() is sentinel_fastembed


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


def test_torch_backend_missing_gives_actionable_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # simulate not installed
    ri._load_torch_embedder.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=r"turbo-memory-mcp\[torch\]"):
            ri._load_torch_embedder()
    finally:
        ri._load_torch_embedder.cache_clear()


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
    drops it far below 0.99. The PyTorch reference is dev-only (dependency
    group), so this skips in a bare client install.
    """
    pytest.importorskip("sentence_transformers")

    phrases = [
        "The daemon acquires the lockfile before serving requests.",
        "Виправлено помилку зі станом гонитви у черзі підтверджень.",
        "La configuración del servidor se guarda en un archivo JSON.",
        "缓存索引在重启后仍然有效。",
    ]
    reference = ri._load_torch_embedder().encode(phrases)
    candidate = ri._load_fastembed_embedder().encode(phrases)

    for phrase, ref_vec, cand_vec in zip(phrases, reference, candidate, strict=True):
        similarity = _cosine([float(v) for v in ref_vec], cand_vec)
        assert similarity >= 0.99, (
            f"Backend drift on {phrase!r}: cosine {similarity:.4f} < 0.99 — "
            "fastembed's ONNX conversion no longer matches the PyTorch reference"
        )


def test_write_time_hint_catches_cross_lingual_twin(tmp_path) -> None:
    """The maintenance mechanism that prevents bilingual twin notes: saving a
    UK translation of an existing EN note must surface the original in
    similar_notes at write time (real ONNX model, real cosine scores). The
    legacy twins in this repository predate this hint path — this test pins
    that NEW twins cannot slip in silently."""
    from turbo_memory_mcp.server import remember_note_impl

    project_root = tmp_path / "repo"
    project_root.mkdir()
    env = {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-xling",
        "TQMEMORY_PROJECT_NAME": "XLing",
    }

    first = remember_note_impl(
        "Release v9.9 published on GitHub",
        "Published release v9.9 on GitHub: changelog updated, wheel built, tag pushed.",
        kind="handoff",
        environ=env,
    )
    en_note_id = first["item"]["item_id"]

    second = remember_note_impl(
        "Опубліковано реліз v9.9 на GitHub",
        "Опублікували реліз v9.9 на GitHub: оновлено changelog, зібрано колесо, запушено тег.",
        kind="handoff",
        environ=env,
    )

    hints = second.get("similar_notes", [])
    assert hints, "cross-lingual twin produced no write-time similarity hint"
    assert hints[0]["item_id"] == en_note_id
    assert hints[0]["score"] >= 0.78
    assert hints[0]["suggestion"] in {"supersede_candidate", "review_for_conflict"}
