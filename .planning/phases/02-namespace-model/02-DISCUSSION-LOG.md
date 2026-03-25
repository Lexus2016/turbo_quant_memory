# Phase 2: Namespace Model - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `02-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 2-Namespace Model
**Areas discussed:** Project identity and detection, Storage topology, Hybrid conflict resolution, Write and promotion policy, Provenance contract

---

## Project Identity and Detection

| Option | Description | Selected |
|--------|-------------|----------|
| `git remote URL` first, fallback to repo-root path hash, plus explicit override | Best balance between stable identity, zero-config UX, and local-only repos | ✓ |
| Always hash the absolute repo path | Simplest, but the same repo in another location becomes a different project | |
| Only explicit `project_id` in config | Most deterministic, but weak zero-config UX | |
| Other | Freeform alternative | |

**User's choice:** `git remote URL` first, fallback to repo-root path hash, plus explicit override
**Notes:** This should still work when no remote exists. Phase 2 must treat local-only repositories as first-class citizens.

---

## Storage Topology

| Option | Description | Selected |
|--------|-------------|----------|
| Everything in `~/.turbo-quant-memory/`, repo only carries lightweight config/manifest | Simplest for multi-project reuse and keeps data out of the repo | ✓ |
| `project` in repo, `global` in home-dir | Keeps local memory near code, but adds cleanup and portability friction | |
| Fully central store plus custom path override in v1 | Flexible, but more configuration surface up front | |
| Other | Freeform alternative | |

**User's choice:** Everything in `~/.turbo-quant-memory/`, repo only carries lightweight config/manifest
**Notes:** Central storage was preferred over mixing persistent memory data into repositories.

---

## Hybrid Conflict Resolution

| Option | Description | Selected |
|--------|-------------|----------|
| Merged ranking with strong `project` bonus; `global` never overrides a clearly better local hit | Best reuse without losing project-local correctness | ✓ |
| Strict fallback: search `project` first, only use `global` if no good local results exist | Simpler, but weaker cross-project recall | |
| Return two separate buckets: `project_hits` and `global_hits` | Most explicit, but noisier for agents | |
| Other | Freeform alternative | |

**User's choice:** Merged ranking with strong `project` bonus
**Notes:** Phase 2 should keep deterministic tie-breaking so the same query does not reorder unpredictably.

---

## Write and Promotion Policy

| Option | Description | Selected |
|--------|-------------|----------|
| All writes default to `project`; `global` only through explicit promotion | Safest protection against cross-project contamination | ✓ |
| Allow direct writes to `global` when the caller explicitly sets `scope=\"global\"` | More flexible, but easier to pollute global memory | |
| Auto-promote to `global` through heuristics in v1 | Powerful later, but too risky for the namespace foundation phase | |
| Other | Freeform alternative | |

**User's choice:** Builder decision based on safety and quality: all writes default to `project`; `global` only through explicit promotion
**Notes:** Chosen to maximize signal quality in `global` and keep the public Phase 2 write path conservative. Direct global writes can exist later as admin/migration behavior if needed, but not as the normal agent flow.

---

## Provenance Contract

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal envelope | Lowest token cost, but weaker trust and debugging signals | |
| Standard envelope | Best balance of contextual quality, trust, and token cost | ✓ |
| Extended lineage/debug envelope | Strongest observability, but too heavy for default responses | |
| Other | Freeform alternative | |

**User's choice:** Builder decision based on “quality of context at minimum volume”: compact standard envelope
**Notes:** The default envelope should always carry `scope`, `project_id`, `project_name`, `source_kind`, `item_id` or `block_id`, `source_path`, `updated_at`, `confidence`, and `can_hydrate`, plus `promoted_from` when relevant. Heavier lineage/debug data should stay out of default responses.

---

## the agent's Discretion

- Exact git remote normalization logic
- Exact hash format and storage manifest naming
- Exact project-bias weighting formula
- Exact filesystem subdirectory layout inside `~/.turbo-quant-memory/`

## Deferred Ideas

- Team scope between `project` and `global`
- Auto-promotion heuristics from `project` to `global`
- Richer default debug lineage in retrieval responses
