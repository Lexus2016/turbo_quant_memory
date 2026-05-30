# User-Flagged Memory (provenance) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent persist user-flagged knowledge with a `provenance` marker (`human-explicit` vs `agent`) and rank human-explicit notes above agent-inferred ones of equal relevance.

**Architecture:** Add an optional `provenance` field to the note record, defaulted and normalized on read (lazy — no migration, no format-version bump, no LanceDB column). `remember_note` threads the value through. Retrieval reads the canonical note JSON (already done in `_decorate_candidate`) and applies a small additive bonus in `_query_scope` for `human-explicit` notes, mirroring the existing `MARKDOWN_KIND_BONUS` pattern.

**Tech Stack:** Python 3, pytest, existing `turbo_memory_mcp` package (no new dependencies).

**Backward compatibility:** `provenance` is optional everywhere. Existing note JSONs without the field normalize to `"agent"` on read. No on-disk migration runs; no other project's data is touched.

---

### Task 1: Provenance constants and normalizer (`store.py`)

**Files:**
- Modify: `src/turbo_memory_mcp/store.py`
- Test: `tests/test_provenance.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_provenance.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provenance.py -v`
Expected: FAIL with `ImportError` (symbols not defined in store.py).

- [ ] **Step 3: Add constants after the tier block**

In `src/turbo_memory_mcp/store.py`, immediately after the line `DEFAULT_SEARCH_TIERS = (NOTE_TIER_DURABLE, NOTE_TIER_REFERENCE)` (currently line 41), add:

```python
# User-flagged memory: who created this note. `human-explicit` = the user
# explicitly ordered it remembered; `agent` = the agent wrote it on its own
# initiative. Used to rank human-flagged knowledge above agent guesses.
NOTE_PROVENANCE_HUMAN = "human-explicit"
NOTE_PROVENANCE_AGENT = "agent"
NOTE_PROVENANCES = (NOTE_PROVENANCE_HUMAN, NOTE_PROVENANCE_AGENT)
DEFAULT_PROVENANCE = NOTE_PROVENANCE_AGENT
```

- [ ] **Step 4: Add the normalizer after `normalize_note_status`**

In `src/turbo_memory_mcp/store.py`, immediately after the `normalize_note_status` function (ends at line 832), add:

```python
def normalize_provenance(value: str | None) -> str:
    """Normalize a provenance value. Unknown/empty -> DEFAULT_PROVENANCE.

    Unlike normalize_note_kind, this NEVER raises: provenance is advisory
    metadata and legacy notes lack the field entirely, so a missing or
    unrecognized value degrades gracefully to `agent`.
    """
    if value is None or not str(value).strip():
        return DEFAULT_PROVENANCE
    resolved = str(value).strip().lower()
    if resolved not in NOTE_PROVENANCES:
        return DEFAULT_PROVENANCE
    return resolved
```

- [ ] **Step 5: Export the new symbols**

In the `__all__` list at the bottom of `src/turbo_memory_mcp/store.py`, add these entries (keep the list sorted as it currently is):

```python
    "DEFAULT_PROVENANCE",
    "NOTE_PROVENANCES",
    "NOTE_PROVENANCE_AGENT",
    "NOTE_PROVENANCE_HUMAN",
    "normalize_provenance",
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_provenance.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add src/turbo_memory_mcp/store.py tests/test_provenance.py
git commit -m "feat(store): add provenance constants and normalizer"
```

---

### Task 2: Persist and lazily normalize provenance (`store.py`)

**Files:**
- Modify: `src/turbo_memory_mcp/store.py:270-330` (write methods), `:367-390` (promote_note), `:662-707` (_build_note_record, _normalize_note_record)
- Test: `tests/test_provenance.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provenance.py`:

```python
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    project = ProjectIdentity(
        project_id="proj-test",
        project_name="Test Project",
        project_root=str(tmp_path / "repo"),
    )
    return MemoryStore(project, storage_root=tmp_path / "home")


def test_write_note_with_provenance_roundtrips(tmp_path):
    store = _store(tmp_path)
    note = store.write_project_note(
        "T", "body", note_kind="decision", provenance="human-explicit"
    )
    assert note["provenance"] == "human-explicit"
    reread = store.read_project_note(note["note_id"])
    assert reread["provenance"] == "human-explicit"


def test_write_note_defaults_to_agent(tmp_path):
    store = _store(tmp_path)
    note = store.write_project_note("T", "body", note_kind="lesson")
    assert note["provenance"] == "agent"


def test_legacy_note_without_field_reads_as_agent(tmp_path):
    store = _store(tmp_path)
    note = store.write_project_note("T", "body", note_kind="lesson")
    # Simulate a legacy on-disk note: strip the field and rewrite raw.
    import json

    path = store.project_note_path(note["note_id"])
    raw = json.loads(path.read_text())
    raw.pop("provenance", None)
    path.write_text(json.dumps(raw))
    reread = store.read_project_note(note["note_id"])
    assert reread["provenance"] == "agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provenance.py -v -k "roundtrips or defaults_to_agent or legacy_note"`
Expected: FAIL — `write_project_note` has no `provenance` kwarg / note dict has no `provenance` key.

- [ ] **Step 3: Add `provenance` to `_build_note_record`**

In `src/turbo_memory_mcp/store.py`, modify `_build_note_record`. Add the parameter to its signature (after `tier: str | None = None,` at line 676):

```python
        tier: str | None = None,
        provenance: str | None = None,
```

And in the `note = {...}` dict (after the `"tier": resolved_tier,` line, line 689), add:

```python
            "tier": resolved_tier,
            "provenance": normalize_provenance(provenance),
```

- [ ] **Step 4: Thread `provenance` through the write methods**

In `write_project_note` (line 270): add `provenance: str | None = None,` to the signature after `tier: str | None = None,` (line 281), and pass `provenance=provenance,` in the `self._build_note_record(...)` call (after `tier=tier,` at line 293).

In `write_global_note` (line 299): add `provenance: str | None = None,` to the signature after `tier: str | None = None,` (line 312), and pass `provenance=provenance,` in the `self._build_note_record(...)` call (after `tier=tier,` at line 326).

- [ ] **Step 5: Preserve provenance on promotion**

In `promote_note` (line 367), in the `self.write_global_note(...)` call, add after `tier=project_note.get("tier"),` (line 389):

```python
            tier=project_note.get("tier"),
            provenance=project_note.get("provenance"),
```

- [ ] **Step 6: Lazily normalize on read**

In `_normalize_note_record` (line 701), after the `payload["note_status"] = ...` line (line 706), add:

```python
        payload["note_status"] = normalize_note_status(payload.get("note_status"))
        payload["provenance"] = normalize_provenance(payload.get("provenance"))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_provenance.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 8: Run the full suite for regressions**

Run: `pytest -q`
Expected: PASS (no regressions; existing notes round-trip with the new field).

- [ ] **Step 9: Commit**

```bash
git add src/turbo_memory_mcp/store.py tests/test_provenance.py
git commit -m "feat(store): persist note provenance and normalize on read"
```

---

### Task 3: Thread provenance through `remember_note` (`server.py`)

**Files:**
- Modify: `src/turbo_memory_mcp/server.py:148-159` (tool), `:290-302` (dispatch), `:1011-1045` (impl)
- Test: `tests/test_provenance.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provenance.py`:

```python
from turbo_memory_mcp.server import remember_note_impl


def _env(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {
        "TQMEMORY_HOME": str(tmp_path / "home"),
        "TQMEMORY_PROJECT_ROOT": str(repo),
        "TQMEMORY_PROJECT_ID": "proj-test",
        "TQMEMORY_PROJECT_NAME": "Test Project",
    }


def test_remember_note_defaults_to_agent(tmp_path):
    env = _env(tmp_path)
    payload = remember_note_impl(
        "T", "body", kind="lesson", cwd=tmp_path / "repo", environ=env
    )
    assert payload["item"]["provenance"] == "agent"


def test_remember_note_human_explicit(tmp_path):
    env = _env(tmp_path)
    payload = remember_note_impl(
        "T", "body", kind="decision",
        provenance="human-explicit", cwd=tmp_path / "repo", environ=env,
    )
    assert payload["item"]["provenance"] == "human-explicit"
```

> Note: this test asserts `payload["item"]["provenance"]`, which requires the contracts change in Task 5. If running tasks strictly in order, expect these two tests to fail at Step 2 for BOTH the missing kwarg (this task) and the missing payload field (Task 5). They pass after Task 5. To verify this task in isolation, also assert on the stored note via `store.read_project_note`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provenance.py -v -k "remember_note_defaults or remember_note_human"`
Expected: FAIL — `remember_note_impl` has no `provenance` kwarg.

- [ ] **Step 3: Add `provenance` to `remember_note_impl`**

In `src/turbo_memory_mcp/server.py`, modify `remember_note_impl` (line 1011). Add to the signature after `scope: str = "project",` (line 1019):

```python
    scope: str = "project",
    provenance: str = "agent",
```

And change the `store.write_project_note(...)` call (line 1037) to pass it through:

```python
    note = store.write_project_note(
        title, content, note_kind=resolved_kind, tags=tags,
        source_refs=source_refs, provenance=provenance,
    )
```

- [ ] **Step 4: Add `provenance` to the dispatch helper**

In `_tool_remember_note` (line 290), add to the `remember_note_impl(...)` call after `scope=str(kwargs.get("scope", "project")),` (line 297):

```python
        scope=str(kwargs.get("scope", "project")),
        provenance=str(kwargs.get("provenance", "agent")),
```

- [ ] **Step 5: Add `provenance` to the MCP tool surface**

In the `remember_note` tool (line 148), add the parameter after `scope: str = "project",` (line 154):

```python
        scope: str = "project",
        provenance: str = "agent",
```

And add it to the dispatcher dict after `"scope": scope,`:

```python
                "scope": scope,
                "provenance": provenance,
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_provenance.py -v -k "remember_note_defaults or remember_note_human"`
Expected: still FAIL on the `payload["item"]["provenance"]` assertion until Task 5 (the stored note is correct; the payload does not yet surface it). This is expected — proceed to Task 5, then re-run.

- [ ] **Step 7: Commit**

```bash
git add src/turbo_memory_mcp/server.py tests/test_provenance.py
git commit -m "feat(server): thread provenance through remember_note"
```

---

### Task 4: Surface provenance in result payloads (`contracts.py`)

**Files:**
- Modify: `src/turbo_memory_mcp/contracts.py:168-200` (build_note_item_payload), `:249-281` (build_semantic_item_payload), `:316-348` (build_hydrated_note_item_payload)
- Test: `tests/test_provenance.py` (the Task 3 tests now pass)

- [ ] **Step 1: Add provenance to `build_note_item_payload`**

In `src/turbo_memory_mcp/contracts.py`, in `build_note_item_payload` (line 168), add to the `payload` dict after `"updated_at": note["updated_at"],` (line 188):

```python
        "updated_at": note["updated_at"],
        "provenance": note.get("provenance", "agent"),
```

- [ ] **Step 2: Add provenance to `build_semantic_item_payload`**

In `build_semantic_item_payload` (line 249), after the `if item.get("tier"):` block (lines 275-276), add:

```python
    if item.get("provenance"):
        payload["provenance"] = item["provenance"]
```

- [ ] **Step 3: Add provenance to `build_hydrated_note_item_payload`**

In `build_hydrated_note_item_payload` (line 316), after the `if note.get("tier"):` block (lines 336-337), add:

```python
    if note.get("provenance"):
        payload["provenance"] = note["provenance"]
```

- [ ] **Step 4: Run the Task 3 tests (now passing)**

Run: `pytest tests/test_provenance.py -v -k "remember_note_defaults or remember_note_human"`
Expected: PASS — the write payload now carries `provenance`.

- [ ] **Step 5: Commit**

```bash
git add src/turbo_memory_mcp/contracts.py
git commit -m "feat(contracts): surface provenance in note and search payloads"
```

---

### Task 5: Rank human-explicit notes higher (`retrieval.py`)

**Files:**
- Modify: `src/turbo_memory_mcp/retrieval.py:21-24` (constants + imports), `:115-149` (_query_scope), `:215-224` (_decorate_candidate)
- Test: `tests/test_provenance.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provenance.py`:

```python
from unittest.mock import patch

from turbo_memory_mcp.retrieval import semantic_search
from turbo_memory_mcp.server import build_runtime_context


class _KeywordEmbedder:
    KEYWORDS = ("auth", "token", "rotation", "refresh", "session", "cache")

    def encode(self, texts):
        out = []
        for text in texts:
            low = text.lower()
            vec = [0.0] * 384
            for i, kw in enumerate(self.KEYWORDS):
                vec[i] = 1.0 if kw in low else 0.0
            out.append(vec)
        return out


def test_human_explicit_ranks_above_agent(tmp_path):
    env = _env(tmp_path)
    cwd = tmp_path / "repo"
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=_KeywordEmbedder(),
    ):
        # Two notes with identical embedding (same keywords) -> equal base
        # relevance. Only provenance differs.
        remember_note_impl(
            "Agent note", "auth token rotation refresh", kind="lesson",
            provenance="agent", cwd=cwd, environ=env,
        )
        remember_note_impl(
            "Human note", "auth token rotation refresh", kind="lesson",
            provenance="human-explicit", cwd=cwd, environ=env,
        )
        _, store = build_runtime_context(cwd=cwd, environ=env)
        result = semantic_search(store, "auth token rotation refresh",
                                 scope="project", limit=5)
    titles = [item["title"] for item in result["items"]]
    assert titles.index("Human note") < titles.index("Agent note")
    human = next(i for i in result["items"] if i["title"] == "Human note")
    assert human["provenance"] == "human-explicit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provenance.py -v -k human_explicit_ranks`
Expected: FAIL — ordering is tie-broken arbitrarily (no provenance bonus), and/or `provenance` missing from items.

- [ ] **Step 3: Add the bonus constant and import**

In `src/turbo_memory_mcp/retrieval.py`, after `MARKDOWN_KIND_BONUS = 0.02` (line 22), add:

```python
MARKDOWN_KIND_BONUS = 0.02
# Additive bonus for notes the user explicitly flagged (provenance=human-explicit).
# Small enough not to override relevance, large enough to win ties and lift a
# close-but-not-top human note. UNCALIBRATED heuristic — tune on a real corpus
# (see lesson e1b9b1df42094746 on the P1 threshold miscalibration).
PROVENANCE_HUMAN_BONUS = 0.06
```

In the `from .store import (...)` block (lines 11-19), add `NOTE_PROVENANCE_HUMAN,` (keep alphabetical-ish with the others):

```python
    NOTE_PROVENANCE_HUMAN,
    NOTE_SOURCE_KIND,
```

- [ ] **Step 4: Apply the bonus in `_query_scope`**

In `_query_scope` (line 115), inside the `for row in rows:` loop, after the `kind_bonus = ...` line (line 134), add a provenance lookup, and include it in `effective_score` (line 136):

```python
        kind_bonus = MARKDOWN_KIND_BONUS if row.get("source_kind") == MARKDOWN_SOURCE_KIND else 0.0
        provenance_bonus = 0.0
        if row.get("source_kind") == NOTE_SOURCE_KIND:
            # Canonical note JSON is authoritative; the LanceDB mirror has no
            # provenance column. Cheap on our scale (hundreds of small JSONs).
            try:
                cand_note = store.read_note(str(row["note_id"]), scope)
                if cand_note.get("provenance") == NOTE_PROVENANCE_HUMAN:
                    provenance_bonus = PROVENANCE_HUMAN_BONUS
            except Exception:  # noqa: BLE001 — bonus is advisory; never break search
                provenance_bonus = 0.0
        score = min(base_score + lexical_bonus, 1.0)
        effective_score = min(score + project_bias + kind_bonus + provenance_bonus, 1.0)
```

> Note: replace the existing two lines `score = ...` and `effective_score = ...` (lines 135-136) with the block above — they are rewritten to include `provenance_bonus`.

- [ ] **Step 5: Surface provenance in the decorated payload**

In `_decorate_candidate` (line 177), inside the `if candidate["source_kind"] == NOTE_SOURCE_KIND:` block, after `payload["note_status"] = note["note_status"]` (line 218), add:

```python
        payload["note_status"] = note["note_status"]
        payload["provenance"] = note.get("provenance") or "agent"
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_provenance.py -v -k human_explicit_ranks`
Expected: PASS — "Human note" ranks above "Agent note" and carries `provenance`.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add src/turbo_memory_mcp/retrieval.py tests/test_provenance.py
git commit -m "feat(retrieval): rank human-flagged notes above agent notes"
```

---

### Task 6: Document the new parameter and agent behavior

**Files:**
- Modify: `README.md` (and `README.uk.md`, `README.ru.md` if kept in sync), `CHANGELOG.md`

- [ ] **Step 1: Add a CHANGELOG entry**

In `CHANGELOG.md`, add a new top entry under an Unreleased/next-version heading:

```markdown
### Added
- `remember_note` now accepts an optional `provenance` parameter
  (`human-explicit` | `agent`, default `agent`). Notes the user explicitly
  asks to remember are flagged `human-explicit` and rank above agent-written
  notes of equal relevance. The field is optional and backward compatible;
  legacy notes read as `agent`. No migration required.
```

- [ ] **Step 2: Document agent behavior in the AI directive**

In `README.md`, in the "Instructions for AI Agents" / "Memory Writing Discipline" section, add one line:

```markdown
- When the USER explicitly asks to remember something ("remember this",
  "save this to my knowledge base"), call `remember_note(..., provenance="human-explicit")`.
  Notes you write on your own initiative keep the default `provenance="agent"`.
```

Mirror the same line into `README.uk.md` / `README.ru.md` if those are maintained in sync (check `git log --oneline -5 -- README.uk.md`).

- [ ] **Step 3: Commit**

```bash
git add README.md README.uk.md README.ru.md CHANGELOG.md
git commit -m "docs: document provenance parameter and agent behavior"
```

---

## Self-Review

**Spec coverage:**
- provenance field + default → Task 1, 2 ✓
- write path threads provenance → Task 2, 3 ✓
- lazy normalize-on-read (no migration) → Task 2 Step 6 ✓
- retrieval boost for human-explicit → Task 5 ✓
- provenance visible in results → Task 4, Task 5 Step 5 ✓
- provenance boundary (agent sets human-explicit only on explicit user command) → Task 6 Step 2 (behavior doc) ✓
- non-goals (no C, no slash, no decay) → not implemented, correct ✓

**Type consistency:** `provenance` (str) used uniformly; `normalize_provenance` returns one of `NOTE_PROVENANCES`; constants `NOTE_PROVENANCE_HUMAN`/`_AGENT`/`DEFAULT_PROVENANCE` referenced identically across store.py, server.py, retrieval.py.

**Ordering caveat (documented):** Task 3's payload assertions depend on Task 4. Flagged inline in Task 3 Step 1/6 so an out-of-order executor is not surprised.

**Calibration risk (documented):** `PROVENANCE_HUMAN_BONUS = 0.06` is an uncalibrated heuristic, flagged in code and referencing the P1 lesson. Tune on a real corpus before relying on exact ranking margins.
