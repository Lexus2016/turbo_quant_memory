from __future__ import annotations

import pytest

from turbo_memory_mcp.store import (
    DEFAULT_PROVENANCE,
    NOTE_PROVENANCE_AGENT,
    NOTE_PROVENANCE_HUMAN,
    NOTE_PROVENANCES,
    normalize_provenance,
)


def test_default_provenance_is_agent():
    assert DEFAULT_PROVENANCE == NOTE_PROVENANCE_AGENT == "agent"
    assert NOTE_PROVENANCE_HUMAN == "human-explicit"
    assert set(NOTE_PROVENANCES) == {"human-explicit", "agent"}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("human-explicit", "human-explicit"),
        ("HUMAN-EXPLICIT", "human-explicit"),
        ("  agent  ", "agent"),
        ("agent", "agent"),
        (None, "agent"),
        ("", "agent"),
        ("nonsense", "agent"),  # graceful fallback (unlike note_kind which raises)
    ],
)
def test_normalize_provenance(value, expected):
    assert normalize_provenance(value) == expected
