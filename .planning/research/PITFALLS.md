# Pitfalls Research

## 1. Overcompression Hides Critical Detail

- Warning signs: search results look plausible but omit edge cases, file-local assumptions, or version caveats.
- Попереджувальні сигнали: результати пошуку виглядають правдоподібно, але втрачають edge cases, локальні припущення файлу або version caveats.
- Prevention: always return provenance, expose hydration tools, and include low-confidence markers when compression is aggressive.
- Профілактика: завжди повертати provenance, експортувати hydration-tools і додавати low-confidence markers, коли стиснення агресивне.
- Phase mapping: Phase 3 and Phase 4.
- Мапінг на фази: Фаза 3 і Фаза 4.

## 2. Stale Index Drift

- Warning signs: changed Markdown files are not reflected in retrieval results.
- Попереджувальні сигнали: змінені Markdown-файли не відображаються в retrieval results.
- Prevention: track file hashes or mtimes and support incremental reindex.
- Профілактика: відстежувати file hashes або mtimes і підтримувати incremental reindex.
- Phase mapping: Phase 2.
- Мапінг на фази: Фаза 2.

## 3. Hard-to-Install Stack

- Warning signs: setup requires extra services, GPU-only dependencies, or multiple manual steps.
- Попереджувальні сигнали: setup вимагає додаткових сервісів, GPU-only залежностей або багатьох ручних кроків.
- Prevention: keep MVP to stdio MCP + embedded DB + local CPU embeddings, and document one blessed install path.
- Профілактика: для MVP триматися stdio MCP + embedded DB + local CPU embeddings і задокументувати один blessed install path.
- Phase mapping: Phase 1 and Phase 5.
- Мапінг на фази: Фаза 1 і Фаза 5.

## 4. Memory Becomes a Dump, Not a Tool

- Warning signs: write-back notes accumulate without tags, source, or retrieval value.
- Попереджувальні сигнали: write-back notes накопичуються без тегів, джерела або retrieval-value.
- Prevention: enforce note schema, tags, timestamps, and optional project/session metadata.
- Профілактика: жорстко задати schema для note, теги, timestamps і необов'язкові project/session metadata.
- Phase mapping: Phase 4.
- Мапінг на фази: Фаза 4.

## 5. Prompt Injection Through Retrieved Notes

- Warning signs: stored notes contain instructions that try to override user intent or system behavior.
- Попереджувальні сигнали: збережені notes містять інструкції, які намагаються перевизначити user intent або system behavior.
- Prevention: mark memory as untrusted content, preserve source boundaries, and avoid mixing tool text with control instructions.
- Профілактика: позначати memory як untrusted content, зберігати межі джерела і не змішувати tool-text із control instructions.
- Phase mapping: Phase 3 and Phase 5.
- Мапінг на фази: Фаза 3 і Фаза 5.
