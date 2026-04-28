created: 2026-04-28
---
title: Fix lint_knowledge_base false positives (Cyrillic titles, ignored-dir broken links)
area: knowledge_lint
files:
  - src/turbo_memory_mcp/knowledge_lint.py:17
  - src/turbo_memory_mcp/knowledge_lint.py:79
  - src/turbo_memory_mcp/knowledge_lint.py:188
  - src/turbo_memory_mcp/ingestion.py:15
---

## Problem

`mcp__tqmemory__lint_knowledge_base` reports two classes of false positives that pollute the issue list and erode trust in the lint tool:

### 1. Non-ASCII titles collapse to `title_key="untitled"`

`_TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")` at `knowledge_lint.py:17` strips every character outside ASCII `[a-z0-9]`, so any Cyrillic, Greek, CJK, or accented Latin H1 normalizes to the empty string and falls through to the `"untitled"` sentinel at line 210. Six real, distinct files in this repo collide into a single bogus duplicate-title group:

```
duplicate_title  title_key="untitled"
  CLIENT_INTEGRATIONS.ru.md   (# Интеграции Клиентов)
  CLIENT_INTEGRATIONS.uk.md   (# Інтеграції Клієнтів)
  MEMORY_STRATEGY.ru.md       (# Стратегия Памяти)
  MEMORY_STRATEGY.uk.md       (# Стратегія Пам'яті)
  TECHNICAL_SPEC.ru.md        (# Техническая Спецификация)
  TECHNICAL_SPEC.uk.md        (# Технічна Специфікація)
```

These titles are *unique*, but the regex blanks them all out. The same bug affects any non-English knowledge base, which directly contradicts the project's i18n stance (English/Ukrainian/Russian docs).

### 2. Links into `DEFAULT_IGNORED_DIR_NAMES` directories are reported as broken

`_iter_markdown_files()` at `knowledge_lint.py:188` skips every directory named in `DEFAULT_IGNORED_DIR_NAMES` (defined at `ingestion.py:15`), including `benchmarks`. The lint then builds `source_set` from the surviving files and flags any link whose resolved target is *not* in that set as a `broken_link`, even when the target file physically exists on disk.

In this repo three real files trigger the false positive:

```
broken_link  README.md     -> benchmarks/latest.md       (file exists)
broken_link  README.ru.md  -> benchmarks/latest.ru.md    (file exists)
broken_link  README.uk.md  -> benchmarks/latest.uk.md    (file exists)
```

The same trap applies to any link pointing into `.planning`, `dist`, `build`, etc. Lint thus contradicts the human reader: the link works in any Markdown viewer but lint says it's broken.

## Solution

### Fix 1 — Unicode-aware title normalization

Replace the ASCII-only regex with a Unicode-aware slugify so Cyrillic/CJK/accented titles produce stable, distinct keys:

```python
# knowledge_lint.py:17
_TITLE_NORMALIZE_RE = re.compile(r"[^\w\d]+", re.UNICODE)

def _normalize_title(title: str) -> str:
    normalized = _TITLE_NORMALIZE_RE.sub("-", title.casefold()).strip("-")
    return normalized or "untitled"
```

`\w` is Unicode-aware in Python 3 by default (covers letters in any script). `str.casefold()` is the Unicode-correct lower-case (handles German `ß`, Greek `Σ`, etc.).

Add a regression test asserting that `# Стратегія Пам'яті` and `# Strategy Notes` produce different `title_key` values.

### Fix 2 — Lint should resolve links against on-disk filesystem, not just indexed corpus

Two options, ordered by preference:

(a) **Disk-aware resolver (preferred)**: when a link target is missing from `source_set`, do one final `(root_path / target).is_file()` probe before flagging it as broken. Cheap, low-risk, eliminates the false positive without changing what gets indexed.

(b) **Lint-only ignore list**: introduce a separate, narrower ignore set for lint (e.g., only `.git`, `.venv`, `node_modules`, caches) so first-class documentation directories (`benchmarks/`, `.planning/` if user opts in) participate in cross-link checks. More invasive; defer unless (a) proves insufficient.

Add a regression test: a fixture repo with `README.md` linking to `benchmarks/latest.md` must yield zero `broken_link` issues.

## Out of scope

- Re-enabling indexing of `benchmarks/` or `.planning/` for `semantic_search` — those exclusions exist for retrieval signal-to-noise reasons and are correct as-is.
- Changing `_extract_title` to walk past the first H1 (current behaviour is fine; the bug is in normalization, not extraction).
