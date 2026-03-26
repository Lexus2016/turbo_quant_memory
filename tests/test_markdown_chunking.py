from __future__ import annotations

from turbo_memory_mcp.markdown_parser import PREAMBLE_HEADING, build_block_id, parse_markdown_blocks


def test_parser_preserves_preamble_before_first_heading() -> None:
    markdown = """
Intro line before the first heading.

Still in the preamble.

# Architecture

Primary system section.
""".strip()

    blocks = parse_markdown_blocks(markdown)

    assert blocks[0].heading_path == (PREAMBLE_HEADING,)
    assert "Intro line before the first heading." in blocks[0].content_raw
    assert blocks[1].heading_path == ("Architecture",)


def test_parser_preserves_nested_heading_paths() -> None:
    markdown = """
# Architecture

Top-level context.

## Storage

Storage details.

### ADR-001

Implementation decision.
""".strip()

    blocks = parse_markdown_blocks(markdown)
    heading_paths = [block.heading_path for block in blocks]

    assert ("Architecture",) in heading_paths
    assert ("Architecture", "Storage") in heading_paths
    assert ("Architecture", "Storage", "ADR-001") in heading_paths


def test_parser_splits_oversized_sections_without_regex_only_logic() -> None:
    large_paragraph = "alpha " * 180
    markdown = f"""
# Architecture

First paragraph.

```python
def example() -> str:
    return "code fence"
```

{large_paragraph}

Second paragraph after the oversized chunk.
""".strip()

    blocks = parse_markdown_blocks(markdown, soft_limit=120)

    assert len(blocks) >= 3
    assert all(block.heading_path == ("Architecture",) for block in blocks)
    assert blocks[0].content_raw.startswith("# Architecture")
    assert "```python" in "\n\n".join(block.content_raw for block in blocks)


def test_block_id_is_stable_when_content_changes_but_location_does_not() -> None:
    before = """
# Architecture

Old text.
""".strip()
    after = """
# Architecture

New text with extra context.
""".strip()

    before_block = parse_markdown_blocks(before)[0]
    after_block = parse_markdown_blocks(after)[0]

    before_id = build_block_id("docs-root", "architecture/adr-001.md", before_block.heading_path, before_block.chunk_index)
    after_id = build_block_id("docs-root", "architecture/adr-001.md", after_block.heading_path, after_block.chunk_index)

    assert before_id == after_id
    assert before_block.block_checksum != after_block.block_checksum
