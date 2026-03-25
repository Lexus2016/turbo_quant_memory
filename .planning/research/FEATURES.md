# Features Research

## Table Stakes

- Markdown folder indexing with stable chunk IDs.
- Індексація папок Markdown зі stable chunk IDs.
- Semantic search that returns the most relevant blocks instead of full files.
- Semantic search, який повертає найбільш релевантні блоки замість повних файлів.
- Source traceability for every returned answer fragment.
- Traceability до джерела для кожного повернутого фрагмента відповіді.
- On-demand expansion from compressed result to fuller context.
- On-demand розширення від стислого результату до повнішого контексту.
- Persistent write-back memory for important session learnings.
- Персистентний write-back memory для важливих висновків із сесій.
- Fast local setup with no separate DB server.
- Швидке локальне налаштування без окремого DB-сервера.

## Differentiators

- Heading-aware, token-saving compressed recall rather than naive chunk dumps.
- Heading-aware, token-saving compressed recall замість naive chunk dump.
- Hybrid hot/cold memory flow: compressed by default, hydrated only when confidence drops.
- Гібридний hot/cold memory flow: за замовчуванням стиснення, а hydrate лише коли падає впевненість.
- Session memory that captures decisions, lessons, and reusable snippets as first-class searchable memory blocks.
- Session memory, який зберігає рішення, уроки та повторно використовувані фрагменти як first-class searchable memory blocks.
- Quality guardrails that tell the agent when to request fuller context.
- Quality guardrails, які підказують агенту, коли треба запросити повніший контекст.

## Anti-Features

- Returning giant raw file bodies by default.
- Повернення великих сирих тіл файлів за замовчуванням.
- Mandatory cloud account or hosted database.
- Обов'язковий cloud-акаунт або hosted database.
- UI-heavy admin console in MVP.
- UI-heavy admin console в MVP.
- Full general-purpose knowledge graph platform before the core memory loop works.
- Повноцінна general-purpose knowledge graph платформа до того, як запрацює базовий memory-loop.

## Complexity Notes

- Indexing and chunking: Low to Medium complexity.
- Індексація і chunking: Низька до середньої складність.
- Retrieval plus compressed rendering: Medium complexity.
- Retrieval разом зі compressed rendering: Середня складність.
- Write-back memory and incremental reindex: Medium complexity.
- Write-back memory і incremental reindex: Середня складність.
- Quality-aware hydration strategy: Medium to High complexity.
- Quality-aware hydration strategy: Середня до високої складність.

## Dependency Notes

- Semantic search depends on chunking and embeddings.
- Semantic search залежить від chunking та embeddings.
- Compressed recall depends on a stable metadata model and source references.
- Compressed recall залежить від стабільної моделі метаданих і посилань на джерела.
- Hydration depends on deterministic source mapping from block to file and heading range.
- Hydration залежить від детермінованого mapping від блока до файлу і діапазону заголовка.
