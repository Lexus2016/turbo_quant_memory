from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from benchmark_retrieval_quality import aggregate, hit_at_k, reciprocal_rank  # noqa: E402


def test_reciprocal_rank_positions() -> None:
    assert reciprocal_rank(["a", "b", "c"], "a") == 1.0
    assert reciprocal_rank(["a", "b", "c"], "b") == 0.5
    assert reciprocal_rank(["a", "b", "c"], "c") == 1 / 3
    assert reciprocal_rank(["a", "b", "c"], "z") == 0.0
    assert reciprocal_rank([], "a") == 0.0


def test_hit_at_k_boundaries() -> None:
    ranked = ["a", "b", "c", "d", "e"]
    assert hit_at_k(ranked, "a", 1) is True
    assert hit_at_k(ranked, "b", 1) is False
    assert hit_at_k(ranked, "c", 3) is True
    assert hit_at_k(ranked, "d", 3) is False
    assert hit_at_k(ranked, "e", 5) is True
    assert hit_at_k(ranked, "z", 5) is False


def test_aggregate_empty() -> None:
    out = aggregate([])
    assert out == {"cases": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "mrr": 0.0}


def test_aggregate_math() -> None:
    cases = [
        {"rr": 1.0, "hit@1": True, "hit@3": True, "hit@5": True},
        {"rr": 0.5, "hit@1": False, "hit@3": True, "hit@5": True},
        {"rr": 0.0, "hit@1": False, "hit@3": False, "hit@5": False},
        {"rr": 0.25, "hit@1": False, "hit@3": False, "hit@5": True},
    ]
    out = aggregate(cases)
    assert out["cases"] == 4
    assert out["hit@1"] == 0.25
    assert out["hit@3"] == 0.5
    assert out["hit@5"] == 0.75
    assert out["mrr"] == (1.0 + 0.5 + 0.0 + 0.25) / 4
