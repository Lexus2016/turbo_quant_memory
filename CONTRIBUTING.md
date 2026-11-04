# Contributing to Turbo Quant Memory

[Українська](CONTRIBUTING.uk.md) · [Русский](CONTRIBUTING.ru.md)

Use Python 3.11+ and uv. Clone the repository and run the same checks as [CI](.github/workflows/ci.yml):

```bash
uv sync --frozen
uv run --no-sync ruff check src tests
uv run --no-sync mypy src/turbo_memory_mcp
uv run --no-sync pytest -q
```

Dependencies and embedding models may need network access during setup. Use disposable project data for tests; do not point experiments at your only copy of an existing memory store.

For a bug report, include the package version, OS, Python version, MCP client, a minimal reproduction, and sanitized diagnostics. Keep secrets, private notes, local database files, and provider credentials out of issues, fixtures, and screenshots.

Keep changes focused. Add a regression test for behavior changes. Preserve migration compatibility and distinguish project from global memory. Read [AGENTS.md](AGENTS.md) for repository conventions and [the agent manual](src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md) before changing tool contracts or installation flows.

Keep user-facing changes synchronized across English, Ukrainian, and Russian. README.md is also the PyPI description: use absolute GitHub URLs for its repository links and images. Do not hand-edit the generated TQ-STATS block or treat its historical example as a current benchmark.

In a pull request, state the problem, changes, commands run, and actual results, including skips or blockers. Source-backed claims about local storage must distinguish the server from the connected MCP client's data handling. The [MIT license](LICENSE) remains unchanged.
