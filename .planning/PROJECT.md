# Turbo Quant Memory for AI Agents

## What This Is

Turbo Quant Memory for AI Agents is a local-first MCP server for AI coding agents that stores large Markdown knowledge bases outside the active chat context and returns compressed, task-relevant context on demand. It is inspired by TurboQuant's compression mindset, but it operates at the memory and retrieval layer rather than modifying model internals or KV cache behavior.

Turbo Quant Memory for AI Agents є local-first MCP-сервером для AI coding-агентів, який зберігає великі Markdown-бази знань поза активним контекстом чату і повертає стислий, релевантний до завдання контекст за запитом. Він натхненний підходом TurboQuant до стиснення, але працює на рівні пам'яті та retrieval, а не змінює внутрішню KV-cache логіку моделі.

## Core Value

Agents can offload cold project context and recover only the minimum high-signal context needed to act correctly.

Агенти можуть виносити "холодний" контекст проєкту з активного діалогу і повертати лише мінімальний високосигнальний контекст, потрібний для правильної роботи.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] AI agents such as Claude Code, Codex, Cursor, OpenCode, and Antigravity can connect to the same memory server through MCP.
- [ ] AI-агенти на кшталт Claude Code, Codex, Cursor, OpenCode та Antigravity можуть підключатися до одного й того самого memory server через MCP.
- [ ] Retrieval defaults to compressed context cards, with full hydration available only when needed.
- [ ] Retrieval за замовчуванням повертає стислі context cards, а повне hydrate-повернення доступне лише за потреби.
- [ ] Setup and connection must stay extremely simple: local install, stdio MCP, minimal config, CPU-friendly defaults.
- [ ] Налаштування і підключення мають залишатися максимально простими: локальна інсталяція, stdio MCP, мінімальний конфіг, CPU-friendly дефолти.
- [ ] Memory must work both per project and across all projects through clear namespace rules.
- [ ] Пам'ять має працювати і в межах одного проєкту, і між усіма проєктами через чіткі namespace-правила.
- [ ] The server must allow agents to write back important learnings and reuse them in later sessions.
- [ ] Сервер має дозволяти агентам записувати важливі висновки і повторно використовувати їх у наступних сесіях.

### Out of Scope

- Direct modification of Claude or Anthropic model KV cache — this project works at the MCP memory layer only.
- Пряма модифікація KV cache Claude або моделей Anthropic — цей проєкт працює лише на рівні MCP-пам'яті.
- Managed multi-tenant cloud platform — MVP stays local-first for fast deployment and low operational overhead.
- Керована multi-tenant cloud-платформа — MVP залишається local-first для швидкого розгортання і низьких операційних витрат.
- Mandatory GPU inference stack — CPU-first operation is required for easy adoption.
- Обов'язковий GPU-стек для інференсу — для легкого старту потрібна робота на CPU.

## Context

- TurboQuant research from Google Research, published on March 24, 2026, shows that aggressive compression plus selective recovery can preserve usefulness while sharply reducing memory costs. The relevant product inference for this project is not "we can change Claude's KV cache", but "we can aggressively compress external context and recover full fidelity only when needed."
- Дослідження TurboQuant від Google Research, опубліковане 24 березня 2026 року, показує, що агресивне стиснення разом із вибірковим відновленням може зберігати корисність і різко знижувати витрати пам'яті. Важливий продуктовий висновок для цього проєкту не в тому, що "ми можемо змінити KV cache Claude", а в тому, що "ми можемо агресивно стискати зовнішній контекст і повертати повну точність лише за потреби".
- Official Anthropic Claude Code documentation confirms MCP servers can be connected through `claude mcp add ...` or `.mcp.json`, which supports a simple local stdio deployment model.
- Офіційна документація Anthropic Claude Code підтверджує, що MCP-сервери можна підключати через `claude mcp add ...` або `.mcp.json`, що підходить для простої локальної stdio-моделі розгортання.
- Official OpenAI Codex documentation confirms MCP servers can be added via `codex mcp add ...`, and official Cursor and OpenCode docs confirm project-level and user-level MCP configuration. A current Flutter integration guide also documents Antigravity setup for custom MCP servers, which is a strong compatibility signal for the same architecture.
- Офіційна документація OpenAI Codex підтверджує, що MCP-сервери можна додавати через `codex mcp add ...`, а офіційна документація Cursor і OpenCode підтверджує project-level та user-level конфігурацію MCP. Актуальний Flutter integration guide також документує налаштування Antigravity для кастомних MCP-серверів, що є сильним сигналом сумісності для цієї самої архітектури.
- Official MCP Python SDK documentation provides a simple Python server path with FastMCP and stdio support, which fits the project's deployment constraint.
- Офіційна документація MCP Python SDK дає простий шлях до Python-сервера через FastMCP і stdio, що відповідає обмеженню про просте розгортання.
- LanceDB and Sentence Transformers both support a local embedded workflow suitable for a file-backed, CPU-usable MVP.
- LanceDB і Sentence Transformers обидва підтримують локальний embedded-режим, придатний для file-backed MVP, який можна використовувати на CPU.

## Constraints

- **Tech stack**: Python 3.11+, MCP Python SDK, embedded local storage, CPU-friendly embeddings — to keep installation and maintenance simple.
- **Техстек**: Python 3.11+, MCP Python SDK, embedded локальне сховище, CPU-friendly embeddings — щоб інсталяція і підтримка були простими.
- **Deployment**: Must run locally over stdio and connect to Claude Code without any hosted dependency.
- **Розгортання**: Має запускатися локально через stdio і підключатися до агент-клієнтів без будь-якої hosted-залежності.
- **Compatibility**: The same core server should be usable from Claude Code, Codex, Cursor, OpenCode, and Antigravity with client-specific config wrappers only.
- **Сумісність**: Той самий core-server має використовуватися з Claude Code, Codex, Cursor, OpenCode і Antigravity, а різнитися повинні лише client-specific config wrappers.
- **Performance**: Retrieval should feel interactive on a laptop-class machine for small and medium knowledge bases.
- **Продуктивність**: Retrieval має відчуватися інтерактивним на звичайному ноутбуці для малих і середніх баз знань.
- **Quality**: Compression must preserve source traceability and provide a clear upgrade path to fuller context.
- **Якість**: Стиснення має зберігати traceability до джерела і давати чіткий шлях до повнішого контексту.
- **Security**: Access must stay explicit to user-approved directories; no silent network requirement for core memory flow.
- **Безпека**: Доступ має бути явним лише до user-approved директорій; для базового memory-flow не повинно бути прихованої мережевої залежності.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Build a memory-layer system, not a model patch | Claude Code uses hosted models, so MCP-side compression is the controllable layer | — Pending |
| Use Markdown as the source of truth | Easy onboarding, inspectable storage, low lock-in | — Pending |
| Use an embedded local vector store | Zero separate service and easier deployment | — Pending |
| Default to compressed recall plus on-demand hydration | Maximizes token savings while reducing quality loss on edge cases | — Pending |
| Prefer CPU-friendly local embeddings first | Keeps MVP easy to install and run on developer laptops | — Pending |
| Design namespaces from day one | Project-only memory and cross-project memory must not get mixed accidentally | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `$gsd-transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `$gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check -> still the right priority?
3. Audit Out of Scope -> reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-25 after initialization*
