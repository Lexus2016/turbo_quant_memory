from __future__ import annotations

from pathlib import Path

import pytest

from turbo_memory_mcp.ingestion import MAX_INDEXABLE_FILE_BYTES, _read_source_text


def test_read_source_text_replaces_invalid_utf8(tmp_path: Path) -> None:
    # A few non-UTF-8 bytes must not abort the whole index pass (audit M3).
    path = tmp_path / "bad.md"
    path.write_bytes(b"# title\n\xff\xfe not utf-8\n")
    text = _read_source_text(path, path.stat().st_size)
    assert text is not None
    assert "title" in text  # decoded with replacement, did not raise


def test_read_source_text_skips_oversized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "huge.md"
    path.write_text("x", encoding="utf-8")
    # Pretend it exceeds the cap without materializing 5 MiB on disk.
    assert _read_source_text(path, MAX_INDEXABLE_FILE_BYTES + 1) is None
    assert "skipping oversized markdown" in capsys.readouterr().err


def test_read_source_text_reads_normal_file(tmp_path: Path) -> None:
    path = tmp_path / "ok.md"
    path.write_text("# hello\nworld\n", encoding="utf-8")
    assert _read_source_text(path, path.stat().st_size) == "# hello\nworld\n"
