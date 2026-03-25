# Architecture Research

## Component Boundaries

### 1. MCP Interface Layer

- Exposes tools over stdio for Claude Code.
- Експортує tools через stdio для Claude Code.
- Validates inputs, limits output size, and returns structured results.
- Валідовує input, обмежує розмір output і повертає структуровані результати.

### 2. Memory Orchestrator

- Coordinates indexing, retrieval, compression, hydration, and write-back.
- Координує indexing, retrieval, compression, hydration і write-back.
- Applies policy decisions such as "compressed first" and "hydrate on ambiguity".
- Застосовує policy-рішення на кшталт "спочатку compressed" і "hydrate при неоднозначності".

### 3. Content Store

- Stores source Markdown files plus generated memory notes.
- Зберігає вихідні Markdown-файли плюс згенеровані memory notes.
- Remains human-readable and file-system native.
- Залишається human-readable і нативним до файлової системи.

### 4. Index Store

- Stores vector embeddings and searchable metadata in embedded local storage.
- Зберігає vector embeddings і searchable metadata в embedded локальному сховищі.
- Must support incremental updates and provenance metadata.
- Має підтримувати incremental updates і provenance metadata.

### 5. Compression and Rendering Layer

- Builds compact result cards from source blocks.
- Будує компактні result cards із вихідних блоків.
- Removes low-signal noise while preserving meaning, citations, and escalation paths.
- Прибирає low-signal noise, зберігаючи сенс, citations і шляхи ескалації.

## Data Flow

1. User or agent registers one or more Markdown paths.
2. Користувач або агент реєструє один чи кілька Markdown-шляхів.
3. The server chunks files by headings and fallback size limits.
4. Сервер ділить файли за заголовками і fallback size limits.
5. The embedding pipeline converts chunks to vectors and stores them with metadata.
6. Embedding pipeline перетворює chunks у вектори і зберігає їх разом з метаданими.
7. Claude Code calls a retrieval tool with a task-specific query.
8. Claude Code викликає retrieval-tool із task-specific query.
9. The server returns compressed result cards with source references and confidence hints.
10. Сервер повертає compressed result cards із посиланнями на джерела і confidence hints.
11. If needed, Claude Code calls hydration tools for a fuller block or file range.
12. Якщо потрібно, Claude Code викликає hydration-tools для повнішого блока або діапазону файлу.
13. Important conclusions can be written back into memory notes and reindexed.
14. Важливі висновки можуть записуватися назад у memory notes і переіндексуватися.

## Suggested Build Order

1. MCP server skeleton and config.
2. Скелет MCP-сервера і конфіг.
3. Markdown scanning, chunking, and stable IDs.
4. Сканування Markdown, chunking і stable IDs.
5. Vector index and semantic search.
6. Vector index і semantic search.
7. Compressed rendering with provenance.
8. Compressed rendering із provenance.
9. Hydration and write-back memory.
10. Hydration і write-back memory.
11. Packaging, smoke tests, and integration docs.
12. Пакування, smoke-тести і integration docs.
