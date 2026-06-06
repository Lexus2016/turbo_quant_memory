# Design: Project Identity Stability & Lifecycle Hygiene

- **Date:** 2026-06-06
- **Status:** Feature 1 (identity stability) **implemented** 2026-06-06 — see "What shipped" below. Feature 2 (orphan lifecycle) proposed/deferred.
- **Scope decision:** Approach **#1 refined** — manifest-indexed *sticky* identity resolution (no central registry file). Approach #2 (re-key all buckets off a git root-commit hash) **rejected** — mass re-home, large blast radius, edge cases. Approach #3 (repo-local `.git/tqmemory-id` pin) **deferred** — leaky on non-git trees and fresh clones, adds a second source of truth. Orphan handling: **detect + surface + assisted prune**, never silent auto-delete.
- **Related memory:** lesson `2dcf8eba8f824fef` (identity-split merge procedure), handoff `5311d703f9134fe9` (memory storage audit), code `identity.py:47` (`resolve_project_identity`).

## Problem

`resolve_project_identity` (`identity.py:47`) is a **pure function of the current git/path state**: `project_id = sha256(identity_source)[:16]`, where `identity_source` is the normalized `git remote get-url origin` when a remote exists (`kind=git_remote`), else the absolute repo path (`kind=repo_path`). Two consequences:

1. **Identity split.** Adding a git remote to a repo that already has notes flips the identity source (path → remote) → a brand-new bucket id → the existing notes become invisible to the now-active identity. This happened to "Тяни-Толкай" (37-note path bucket stranded behind a 7-note remote bucket) and was healed by hand.
2. **No lifecycle.** The resolver only ever *creates* buckets (first use of a cwd); nothing ever reaps them. Buckets whose `project_root` no longer exists on disk (e.g. `1` at `/Users/admin/Downloads/1`, `cats-antidetect-llvmpipe-xvfb`) accumulate forever.

## Goal

- An identity that is **stable** (minted once, never flips when a remote is added or removed) and **transparent** (every adoption leaves an inspectable trail; nothing heals silently).
- **Safe** under the genuinely-ambiguous case: the same filesystem path reused by a *different* project over time must NOT inherit the previous project's memory.
- **Surface** orphaned buckets (root missing) through `health`/`server_info` and offer an assisted prune — without ever auto-deleting memory.
- **Minimal blast radius:** existing buckets keep their current ids; no data is re-homed by the upgrade.

## The safety boundary (correctness-critical)

A shared `project_root` is **not** sufficient to adopt a bucket. The resolver adopts an existing same-root bucket **only** when there is no *remote conflict*:

- old bucket had **no** remote, current has a remote (remote added later) → **adopt**;
- old bucket had a remote, current has none / the same remote (remote removed, or unchanged) → **adopt**;
- old bucket had remote `R1`, current has a **different** remote `R2` → **do NOT adopt** → mint a new bucket. This is the "different project cloned into a previously-used path" case; cross-contaminating its memory would be data corruption.

The `TQMEMORY_PROJECT_ID` env override stays **priority 1**, unchanged — an explicit pin always wins and short-circuits all of the below.

## Design

### Data model (`store.py` / project `manifest.json`)
- Project manifest `format_version` **2 → 3**. Two additive fields:
  - `identity_sources: list[str]` — every identity source ever resolved to this bucket (normalized remote URL(s) and/or the absolute root). Seeded from the bucket's current `identity_source`.
  - `identity_history: list[{source, kind, at, reason}]` — append-only audit of mint/adopt events (`reason ∈ {"minted","adopted:remote-added","adopted:remote-removed","migrated"}`).
- A `normalize`-on-read fills `identity_sources` from the legacy single `identity_source` for any manifest that predates the field, so a v2 manifest is safe to read without rewriting (mirrors the provenance design's lazy-normalize approach).

### Resolution (`identity.py`)
`resolve_project_identity` gains awareness of existing buckets via an index built on demand from the manifests already on disk (no new file):

```
root   = resolve_project_root(cwd, environ)
if env TQMEMORY_PROJECT_ID: return override            # priority 1, unchanged
remote = normalized origin url or None

index  = build_source_index(storage_root)              # source_string -> bucket, root -> bucket
                                                        # (read each projects/*/manifest.json once)

# 1. known remote wins (stable, == today's behavior for remote repos)
if remote and index.by_source(remote):  bucket = that; ensure_source(bucket, root); return id_of(bucket)

# 2. known root → adopt unless remote conflict
bucket = index.by_root(root)
if bucket:
    if remote and bucket.remote and bucket.remote != remote:
        pass                                            # different project at same path → mint new (safety boundary)
    else:
        record_source(bucket, remote or str(root),
                      reason="adopted:remote-added" if remote else "adopted:remote-removed")
        return id_of(bucket)

# 3. first time → mint
source = remote or str(root)
return mint(sha256(source)[:16], sources=[source], history=[{minted}])
```

- **Adoption write** appends to `identity_sources` + `identity_history` of the adopted manifest via atomic temp-write + rename; idempotent (re-adding an existing source dedupes). Concurrent resolvers minting the same new project compute the *same* deterministic id, so they converge.
- **Cost:** one tiny JSON read per existing bucket, once per process start (~tens of files). Negligible at this scale; cache if it ever becomes hot.

### Migration (project-manifest v2 → v3)
- Backfill `identity_sources = [current identity_source]`, `identity_history = [{source, kind, at: updated_at, reason: "migrated"}]` for every bucket. Idempotent, no data movement.
- **Split detection (report-only):** during the sweep, group buckets by `project_root`; any root backing ≥2 buckets is emitted as a `detected_splits` report in the migration result / `health`. We do **not** auto-merge — auto-merging by shared root is the unsafe operation the safety boundary forbids; healing stays the manual procedure of lesson `2dcf8eba`.

### Orphan lifecycle (detect + assisted prune)
- **Detect (surface only):** `health` and `server_info` gain `orphaned_buckets` — buckets whose `project_root` does not exist on disk, with `{project_id, project_name, project_root, note_count}`. This is the same trivial computation the audit already performs.
- **Why never auto-delete:** a missing root is not a dead project — an external/network volume may be unmounted, the storage root may be shared across machines, or the directory was temporarily moved. Auto-reaping on path-absence risks destroying live memory.
- **Assisted prune:** a CLI subcommand `prune-orphans` (dry-run default) that **moves** orphan buckets into `staging/orphan-prune-<ts>/` (reversible) and only hard-deletes on an explicit second `--apply --force`-style confirmation. Mirrors the migrate snapshot ergonomics. No silent destruction.

## Edge cases / error handling
- **Repo with no commits / non-git tree:** no remote → `kind=repo_path`, behaves exactly as today; becomes the sticky id. A later remote-add adopts it (case 2). No regression.
- **Shallow clone (no root commit):** irrelevant — we key off the remote URL, not commit history (a reason #2 was rejected).
- **Remote changed (`R1`→`R2`) on the *same* checkout:** treated as a different project (safety boundary) → new bucket. Rare; the conservative choice avoids merging genuinely different upstreams.
- **Moved repo directory (path changes, remote stable):** resolves via the remote (case 1) → same bucket. Path-only buckets that move are not auto-followed (rare; documented limitation).
- **Multi-machine shared storage:** an orphan on machine A may be live on machine B → exactly why prune is assisted, never automatic.
- **Concurrent adoption:** atomic manifest write + idempotent source dedupe; deterministic ids converge.
- **Legacy v2 manifest read:** `identity_sources` lazily filled from `identity_source`; no crash, no forced rewrite.

## Testing
- **Resolution table** (deterministic, no network — stub the git calls):
  - first use, no remote → mints repo_path id; `identity_sources=[path]`.
  - remote added later (same root) → **adopts** the path bucket; `identity_history` gains `adopted:remote-added`; id unchanged.
  - remote removed later → adopts back by root; id unchanged.
  - same root, **different** remote → mints a **new** bucket (safety boundary asserted).
  - known remote present → returns the remote bucket regardless of cwd path.
  - `TQMEMORY_PROJECT_ID` set → override wins, no index consulted.
- **Migration:** v2 manifest backfills `identity_sources`/`identity_history`; idempotent on re-run; two buckets sharing a root appear in `detected_splits`.
- **Orphan detect:** a bucket with a non-existent root appears in `orphaned_buckets`; one with an existing root does not.
- **Prune:** dry-run lists, no filesystem change; apply moves to `staging/` and is reversible; no path is hard-deleted without explicit confirm.
- Update `tests/test_identity.py` (currently locks the pure path/remote behavior at lines 36/49) to the sticky-resolution contract.

## What shipped (Feature 1, 2026-06-06)

Refined toward necessary-minimal vs the original design; capabilities unchanged:

- **No `format_version` bump, no migration.** `identity_sources` is additive and lazy: `write_project_manifest` seeds it from a legacy single `identity_source` and accumulates the current source on every write (union, idempotent). v2 manifests converge without a migration — the same lazy-normalize approach proven by the provenance design. The migration-time split-detection report is dropped from this increment (recomputable on demand later).
- **No timestamped `identity_history`.** The persisted `identity_sources` *set* is the transparency record (you can see a bucket addressed by both a path and a remote). A timestamped audit log was deferred as forensic, not necessary for the stated stability+transparency goal.
- **`identity.py` stays a pure git/path resolver** (its tests are unchanged). All storage awareness lives in one new function `store.reconcile_project_identity(candidate, storage_root)`, wired into the single chokepoint `server.build_runtime_context` (covers every MCP tool and the CLI).
- **`server_info` surface deferred to Feature 2** — the manifest already persists `identity_sources` (inspectable); live echo is better homed next to orphan/`detected_splits` surfacing.

Files: `src/turbo_memory_mcp/store.py` (`reconcile_project_identity`, manifest accumulation, `replace` import), `src/turbo_memory_mcp/server.py` (seam in `build_runtime_context` + import), `tests/test_identity_reconcile.py` (9 tests: adopt-on-remote-add, same-root-different-remote-mints, remote-removed-adopts, source-pin incl. moved checkout, first-time mint, override bypass, manifest accumulation, real-git end-to-end). Full suite: 358 passed.

## Necessity self-check
Each piece is necessary and minimal. Drop `identity_sources` → no way to recognize a returning project → split persists. Drop the **remote-conflict** branch → path-reuse silently cross-contaminates memory (worse than the bug we fix). Drop `identity_history`/`identity_event` → healing becomes the silent magic the user explicitly rejected ("стабільність та прозорість"). Choosing manifest-as-truth over a central `registry.json` removes a hot shared file and a second SSoT without losing any capability. Orphan handling is detect-and-surface only — the destructive half stays assisted, honoring the project-wide principle that the server never silently destroys user memory.
