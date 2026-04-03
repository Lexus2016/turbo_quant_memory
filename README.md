# Turbo Quant Memory for AI Agents

![Turbo Quant Memory hero](assets/readme-hero-en.svg?v=20260328b)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Other languages: [Russian](README.ru.md) | [Ukrainian](README.uk.md)

Turbo Quant Memory is the memory layer that makes AI agents feel like long-term teammates instead of short-term chat sessions.

If you use Claude Code, Codex, Cursor, OpenCode, Gemini CLI, or any MCP client, this is how you keep your institutional knowledge alive between tasks.

## Why It Matters

Most agent workflows fail in the same place: memory.

- Great insights disappear in chat history.
- Every new task restarts from zero.
- Teams re-explain the same architecture again and again.

Turbo Quant Memory fixes this by making your project knowledge persistent, searchable, and reusable.

## Why Teams Choose Turbo Quant Memory

| Typical AI workflow | With Turbo Quant Memory |
|---|---|
| Agents forget context between sessions | Agents can continue from saved project knowledge |
| Decisions stay buried in old threads | Decisions become reusable notes |
| Team knowledge stays inside one person's head | Knowledge becomes shared, searchable, and portable |
| Token budget is wasted on repeated reading | Context is loaded smarter, so more budget goes to reasoning |

## The Core Promise

Your agents stop behaving like temporary assistants and start behaving like members of the team.

## What Makes It Different

- Local-first by design: your memory stays under your control.
- One memory layer for many clients: same knowledge, same standards, same outcomes.
- Built for real delivery: capture decisions, patterns, and handoffs that compound over time.
- Transparent and auditable: memory is explicit, structured, and easy to inspect.

## Quick Start

Install:

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4
turbo-memory-mcp serve
```

Connect in your client as MCP server `tqmemory` with command `turbo-memory-mcp serve`.

Client-specific setup files: [CLIENT_INTEGRATIONS.md](CLIENT_INTEGRATIONS.md)

## Who This Is For

- AI-first engineering teams
- Solo builders running multiple agents
- Product teams that want consistent AI execution quality
- Anyone tired of repeating context every day

## Why Pick This

Choose Turbo Quant Memory if you want:

- faster onboarding for every new task
- fewer repeated mistakes
- stronger continuity across sessions
- higher ROI from every agent run

## Learn More

- Client integrations: [CLIENT_INTEGRATIONS.md](CLIENT_INTEGRATIONS.md)
- Technical spec: [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md)
- Memory strategy: [MEMORY_STRATEGY.md](MEMORY_STRATEGY.md)
- Benchmarks: [benchmarks/latest.md](benchmarks/latest.md)
