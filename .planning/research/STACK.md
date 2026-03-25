# Stack Research

## Recommendation

- **Python 3.11+** for the main implementation. It keeps MCP server development fast, packaging simple, and AI-adjacent libraries readily available.
- **Python 3.11+** для основної реалізації. Це робить розробку MCP-сервера швидкою, пакування простим, а AI-бібліотеки доступними.
- **MCP Python SDK (`mcp`)** as the server framework, with stdio transport for Claude Code compatibility and the lowest deployment friction.
- **MCP Python SDK (`mcp`)** як серверний фреймворк, зі stdio transport для сумісності з Claude Code і мінімального тертя при розгортанні.
- **LanceDB** as the embedded local vector store. Official docs confirm it can run directly against a local filesystem path without a separate service.
- **LanceDB** як embedded локальне vector-сховище. Офіційна документація підтверджує, що воно може працювати напряму з локальним шляхом у файловій системі без окремого сервісу.
- **Sentence Transformers** with a lightweight local model such as `all-MiniLM-L6-v2` for CPU-friendly embeddings.
- **Sentence Transformers** з легкою локальною моделлю на кшталт `all-MiniLM-L6-v2` для CPU-friendly embeddings.
- **Pydantic / pydantic-settings** for structured configuration and environment loading.
- **Pydantic / pydantic-settings** для структурованої конфігурації і завантаження середовища.
- **Frontmatter or lightweight Markdown parsing** plus heading-aware chunking for deterministic block segmentation.
- **Frontmatter або легкий Markdown-парсер** разом із heading-aware chunking для детермінованої сегментації блоків.
- **uv** as the recommended install and run path for low-friction local usage.
- **uv** як рекомендований спосіб інсталяції й запуску для low-friction локального використання.

## Why This Stack

- Official Anthropic docs show Claude Code can connect to local MCP servers through stdio using `claude mcp add ...`, so a Python stdio server is the shortest path to adoption.
- Офіційна документація Anthropic показує, що Claude Code може підключатися до локальних MCP-серверів через stdio за допомогою `claude mcp add ...`, тому Python stdio server є найкоротшим шляхом до adoption.
- Official MCP Python SDK docs show a minimal server path with FastMCP, tools, and resources, which reduces custom protocol work.
- Офіційна документація MCP Python SDK показує мінімальний шлях до сервера через FastMCP, tools і resources, що зменшує обсяг кастомної протокольної роботи.
- LanceDB gives local file-backed persistence and vector search without needing a separate database process.
- LanceDB дає локальну file-backed персистентність і vector search без потреби в окремому database process.
- Sentence Transformers is a mature default for local semantic search and can run on laptops without GPU requirements.
- Sentence Transformers є зрілим дефолтом для локального semantic search і може працювати на ноутбуках без вимог до GPU.

## Recommended Versions

- Python: `>=3.11`
- Python: `>=3.11`
- `mcp`: use the latest compatible stable release from the official MCP Python SDK line.
- `mcp`: використовувати найновіший сумісний stable-реліз з офіційної лінійки MCP Python SDK.
- `lancedb`: use the latest compatible stable release.
- `lancedb`: використовувати найновіший сумісний stable-реліз.
- `sentence-transformers`: use the latest compatible stable release with pinned transitive dependencies in lockfiles.
- `sentence-transformers`: використовувати найновіший сумісний stable-реліз із зафіксованими transitive-залежностями у lockfile.

## What Not To Use For MVP

- Heavy distributed vector databases — they violate the easy deployment goal.
- Важкі distributed vector databases — вони порушують ціль про просте розгортання.
- Mandatory reranker or hosted summarization API — adds setup, latency, and operating cost too early.
- Обов'язковий reranker або hosted summarization API — занадто рано додає налаштування, затримку і витрати.
- Model-specific KV-cache hacking — not compatible with the real integration point for Claude Code.
- Model-specific KV-cache hacking — це не сумісно з реальним integration point для Claude Code.

## Confidence

- MCP Python SDK: High
- MCP Python SDK: Висока
- LanceDB embedded local use: High
- LanceDB embedded local use: Висока
- Sentence Transformers local CPU embeddings: High
- Sentence Transformers local CPU embeddings: Висока
- Exact best default model for all workloads: Medium
- Точна найкраща дефолтна модель для всіх workloads: Середня
