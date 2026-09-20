"""Fixture builder for benchmark_paraphrase.py — the unbiased retrieval harness.

benchmark_paraphrase.py has shipped without ever being run: it takes an external
fixture, and no fixture existed in this repo. The two harnesses that DO run derive
each query from the gold item's own title, which saturates them (notes: Hit@1 0.99,
Hit@3 1.00) and leaves no headroom for any ranking constant to show an effect.

This builds fixtures whose queries do NOT come from the gold note's title, so the
gate/fusion constants become measurable.

Regimes (all mechanical, reproducible, offline, no new dependencies):
  body  : a wordy sentence from the middle of the note BODY, skipping the lede.
          Title tokens are subtracted when scoring candidate sentences, so the
          query shares as little wording with the title as the note allows.
  ident : identifiers mined from the body (CONST_CASE, snake_case, func(), paths,
          dotted.names). The regime where the BM25 lane should earn its place.

SAFETY: the real memory home is only ever READ, and only its note JSONs. No search
is run here, so nothing can trigger an index sync that rewrites LanceDB.

HONESTY: gold is the note a query was mined from. Two near-duplicate notes make that
gold ambiguous and cost the run a hit it arguably earned, so absolute Hit@k here is a
FLOOR, not a point estimate. The load-bearing signal stays the A/B delta between two
configurations over one identical fixture.

    uv run python scripts/build_paraphrase_fixture.py --regime body --limit 300
    uv run python scripts/build_paraphrase_fixture.py --regime ident --limit 300
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REAL_HOME = Path.home() / ".turbo-quant-memory"
OUT_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "fixtures"

MIN_QUERY_WORDS = 6
MAX_QUERY_WORDS = 16
MIN_IDENTS = 2
MAX_IDENTS = 6

_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MD_NOISE = re.compile(r"[`*_>#\[\]()|]+")
_IDENT = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"          # CONST_CASE
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"          # snake_case
    r"|\b\w+\(\)"                                   # func()
    r"|\b\w+(?:\.\w+){1,3}\b"                      # dotted.names / file.ext
    r"|(?:[\w.-]+/){1,}[\w.-]+"                    # paths
)


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD.finditer(text)}


def _clean(text: str) -> str:
    return _MD_NOISE.sub(" ", text).strip()


def _body_query(note: dict[str, Any]) -> str | None:
    """The body sentence that overlaps the title LEAST, so the query is not the title."""
    content = _CODE_FENCE.sub(" ", str(note.get("content") or ""))
    title_tokens = _tokens(str(note.get("title") or ""))
    lines = [line for line in content.splitlines() if line.strip()]
    # Skip the lede: project convention is that the first sentence restates the
    # conclusion, which is the closest thing the body has to the title.
    body = " ".join(lines[1:]) if len(lines) > 1 else ""
    best: tuple[float, str] | None = None
    for raw in _SENTENCE.split(body):
        sentence = _clean(raw)
        words = _WORD.findall(sentence)
        if not MIN_QUERY_WORDS <= len(words) <= MAX_QUERY_WORDS:
            continue
        sentence_tokens = _tokens(sentence)
        if not sentence_tokens:
            continue
        overlap = len(sentence_tokens & title_tokens) / len(sentence_tokens)
        if best is None or overlap < best[0]:
            best = (overlap, sentence)
    return best[1] if best else None


def _ident_query(note: dict[str, Any]) -> str | None:
    """Identifiers mined from the body, in order, deduped, excluding title words."""
    content = str(note.get("content") or "")
    title_tokens = _tokens(str(note.get("title") or ""))
    seen: list[str] = []
    for match in _IDENT.finditer(content):
        token = match.group(0)
        if token.lower() in title_tokens or token in seen:
            continue
        seen.append(token)
        if len(seen) >= MAX_IDENTS:
            break
    return " ".join(seen) if len(seen) >= MIN_IDENTS else None


BUILDERS = {"body": _body_query, "ident": _ident_query}


def _active_notes(notes_dir: Path) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    if not notes_dir.is_dir():
        return notes
    for path in sorted(notes_dir.glob("*.json")):
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a corrupt note must not break the build
        if note.get("note_status") == "active":
            notes.append(note)
    return notes


def build(regime: str, limit: int, project_id: str | None) -> list[dict[str, Any]]:
    projects_dir = REAL_HOME / "projects"
    if project_id is None:
        candidates = [
            (len(list((path / "notes").glob("*.json"))), path.name)
            for path in projects_dir.iterdir()
            if path.is_dir()
        ]
        if not candidates:
            raise SystemExit(f"no projects under {projects_dir}")
        project_id = max(candidates)[1]

    builder = BUILDERS[regime]
    fixture: list[dict[str, Any]] = []
    skipped = 0
    for note in _active_notes(projects_dir / project_id / "notes"):
        query = builder(note)
        if not query:
            skipped += 1
            continue
        fixture.append({
            "project_id": project_id,
            "gold_id": str(note["note_id"]),
            "query": query,
            "tier": note.get("tier"),
            "provenance": note.get("provenance"),
        })
        if len(fixture) >= limit:
            break
    print(f"regime={regime} project={project_id} queries={len(fixture)} skipped={skipped}")
    return fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regime", choices=sorted(BUILDERS), default="body")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--project-id", default=None, help="default: the project with most notes")
    args = parser.parse_args()

    fixture = build(args.regime, args.limit, args.project_id)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.regime}.json"
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
