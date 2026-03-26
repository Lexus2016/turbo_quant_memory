"""Deterministic Markdown parsing and heading-aware chunking helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdown_it.tree import SyntaxTreeNode

from .store import sha256_text

PREAMBLE_HEADING = "__preamble__"
DEFAULT_SOFT_LIMIT = 1000
_FENCE_LINE_RE = re.compile(r"^\s*(```|~~~)")
_PARSER = MarkdownIt("commonmark")


@dataclass(frozen=True, slots=True)
class ParsedMarkdownBlock:
    """Stable block candidate produced from one Markdown document."""

    heading_path: tuple[str, ...]
    chunk_index: int
    content_raw: str
    block_checksum: str


@dataclass(frozen=True, slots=True)
class _HeadingMarker:
    level: int
    title: str
    line_index: int


@dataclass(frozen=True, slots=True)
class _MarkdownSection:
    heading_path: tuple[str, ...]
    heading_line: str | None
    content_raw: str


def parse_markdown_blocks(markdown_text: str, *, soft_limit: int = DEFAULT_SOFT_LIMIT) -> list[ParsedMarkdownBlock]:
    """Parse Markdown into stable heading-aware block candidates."""

    tokens, _tree = parse_markdown_syntax(markdown_text)
    headings = _collect_headings(tokens)
    sections = _build_sections(markdown_text, headings)
    blocks: list[ParsedMarkdownBlock] = []
    for section in sections:
        blocks.extend(_section_to_blocks(section, soft_limit=soft_limit))
    return blocks


def parse_markdown_syntax(markdown_text: str) -> tuple[list[Token], SyntaxTreeNode]:
    """Parse text once and expose both token and tree representations."""

    tokens = _PARSER.parse(markdown_text)
    return tokens, SyntaxTreeNode(tokens)


def build_block_id(
    root_id: str,
    relative_source_path: str,
    heading_path: Sequence[str],
    chunk_index: int,
) -> str:
    """Derive a location-based block id independent from content bytes."""

    normalized_path = Path(relative_source_path).as_posix().lstrip("./")
    heading_key = " > ".join(part.strip() for part in heading_path) or PREAMBLE_HEADING
    location_key = f"{root_id}|{normalized_path}|{heading_key}|{int(chunk_index)}"
    return f"mdblk-{sha256_text(location_key)[:20]}"


def _collect_headings(tokens: Sequence[Token]) -> list[_HeadingMarker]:
    headings: list[_HeadingMarker] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or not token.tag.startswith("h"):
            continue
        level = int(token.tag[1:])
        line_index = token.map[0] if token.map else 0
        inline_token = tokens[index + 1] if index + 1 < len(tokens) else None
        title = inline_token.content.strip() if inline_token and inline_token.type == "inline" else ""
        headings.append(
            _HeadingMarker(
                level=level,
                title=title or f"untitled-h{level}",
                line_index=line_index,
            )
        )
    return headings


def _build_sections(markdown_text: str, headings: Sequence[_HeadingMarker]) -> list[_MarkdownSection]:
    lines = markdown_text.splitlines()
    if not headings:
        content = markdown_text.strip()
        if not content:
            return []
        return [
            _MarkdownSection(
                heading_path=(PREAMBLE_HEADING,),
                heading_line=None,
                content_raw=content,
            )
        ]

    sections: list[_MarkdownSection] = []
    first_heading_line = headings[0].line_index
    preamble = "\n".join(lines[:first_heading_line]).strip()
    if preamble:
        sections.append(
            _MarkdownSection(
                heading_path=(PREAMBLE_HEADING,),
                heading_line=None,
                content_raw=preamble,
            )
        )

    path_stack: list[str] = []
    for index, heading in enumerate(headings):
        while len(path_stack) >= heading.level:
            path_stack.pop()
        path_stack.append(heading.title)
        next_line = headings[index + 1].line_index if index + 1 < len(headings) else len(lines)
        section_lines = lines[heading.line_index:next_line]
        section_text = "\n".join(section_lines).strip()
        if not section_text:
            continue
        heading_line = section_lines[0].strip() if section_lines else None
        sections.append(
            _MarkdownSection(
                heading_path=tuple(path_stack),
                heading_line=heading_line,
                content_raw=section_text,
            )
        )
    return sections


def _section_to_blocks(section: _MarkdownSection, *, soft_limit: int) -> list[ParsedMarkdownBlock]:
    if len(section.content_raw) <= soft_limit:
        return [_build_block(section.heading_path, 0, section.content_raw)]

    if section.heading_path == (PREAMBLE_HEADING,):
        chunk_texts = _chunk_paragraphs(_split_paragraphs(section.content_raw), soft_limit)
        return [_build_block(section.heading_path, index, chunk_text) for index, chunk_text in enumerate(chunk_texts)]

    heading_line = section.heading_line or section.heading_path[-1]
    body = _remove_heading_line(section.content_raw, heading_line)
    if not body.strip():
        return [_build_block(section.heading_path, 0, heading_line)]

    body_limit = max(1, soft_limit - len(heading_line) - 2)
    body_chunks = _chunk_paragraphs(_split_paragraphs(body), body_limit)
    block_texts = [f"{heading_line}\n\n{chunk}".strip() for chunk in body_chunks]
    return [_build_block(section.heading_path, index, chunk_text) for index, chunk_text in enumerate(block_texts)]


def _build_block(heading_path: Sequence[str], chunk_index: int, content_raw: str) -> ParsedMarkdownBlock:
    normalized = content_raw.strip()
    return ParsedMarkdownBlock(
        heading_path=tuple(heading_path),
        chunk_index=chunk_index,
        content_raw=normalized,
        block_checksum=sha256_text(normalized),
    )


def _remove_heading_line(section_text: str, heading_line: str) -> str:
    lines = section_text.splitlines()
    if lines and lines[0].strip() == heading_line.strip():
        return "\n".join(lines[1:]).strip()
    return section_text


def _split_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                paragraphs.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def _chunk_paragraphs(paragraphs: Sequence[str], soft_limit: int) -> list[str]:
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= soft_limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= soft_limit:
            current = paragraph
            continue
        chunks.append(paragraph)
    if current:
        chunks.append(current)
    return chunks


__all__ = [
    "DEFAULT_SOFT_LIMIT",
    "PREAMBLE_HEADING",
    "ParsedMarkdownBlock",
    "build_block_id",
    "parse_markdown_blocks",
    "parse_markdown_syntax",
]
