# Client Integrations / Інтеграції клієнтів

## Goal / Мета

Use **one local stdio MCP server** and adapt it to each client with the thinnest possible config.

Використовувати **один local stdio MCP server** і адаптувати його до кожного клієнта через максимально тонкий конфіг.

Assumed local command:

Базова локальна команда:

```bash
turbo-memory-mcp serve
```

## 1. Claude Code

Verified pattern: official Claude Code docs support `claude mcp add ...`, `.mcp.json`, and project/user scopes.

Підтверджений патерн: офіційна документація Claude Code підтримує `claude mcp add ...`, `.mcp.json` і project/user scopes.

### Proposed command

```bash
claude mcp add --transport stdio --scope project tqmemory -- turbo-memory-mcp serve
```

### Proposed shared config

```json
{
  "mcpServers": {
    "tqmemory": {
      "command": "turbo-memory-mcp",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

## 2. Codex

Verified pattern: current OpenAI Codex docs support MCP configuration and `codex mcp add ...`.

Підтверджений патерн: актуальна документація OpenAI Codex підтримує конфігурацію MCP і `codex mcp add ...`.

### Proposed command

```bash
codex mcp add tqmemory -- turbo-memory-mcp serve
```

### Proposed config direction

- user-level config in Codex config file for cross-project usage
- repo-local config for project-specific memory routing

- user-level конфіг у файлі конфігурації Codex для cross-project usage
- repo-local конфіг для project-specific memory routing

## 3. Cursor

Verified pattern: official Cursor docs support project `.cursor/mcp.json` and user `~/.cursor/mcp.json`.

Підтверджений патерн: офіційна документація Cursor підтримує project `.cursor/mcp.json` і user `~/.cursor/mcp.json`.

### Project config

```json
{
  "mcpServers": {
    "tqmemory": {
      "command": "turbo-memory-mcp",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

### Recommendation

- Use project config when memory must stay repo-specific.
- Use user config only for the global namespace helper.

- Використовувати project config, коли пам'ять має лишатися repo-specific.
- Використовувати user config лише для global namespace helper.

## 4. OpenCode

Verified pattern: official OpenCode docs support local and remote MCP definitions under `mcp`.

Підтверджений патерн: офіційна документація OpenCode підтримує local і remote MCP definitions під ключем `mcp`.

### Proposed config

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "tqmemory": {
      "type": "local",
      "command": ["turbo-memory-mcp", "serve"],
      "enabled": true
    }
  }
}
```

## 5. Antigravity

Verified signal: a current Flutter MCP integration guide documents Antigravity custom MCP setup through its MCP server management UI and raw config flow.

Підтверджений сигнал: актуальний Flutter MCP integration guide документує кастомне підключення MCP у Antigravity через UI керування MCP-серверами і raw config flow.

### Proposed raw config shape

```json
{
  "mcpServers": {
    "tqmemory": {
      "command": "turbo-memory-mcp",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

### Important note

Antigravity should be treated as **verified-compatible in architecture**, but still requires a smoke test against the real product before we call support "production-proven".

Antigravity варто вважати **архітектурно сумісним**, але перед тим як називати підтримку "production-proven", усе одно потрібен smoke test на реальному продукті.

## 6. Recommended Standardization / Рекомендована стандартизація

Use the same MCP server name everywhere:

Скрізь використовувати однакове ім'я MCP-сервера:

`tqmemory`

Use the same launch contract everywhere:

Скрізь використовувати однаковий launch contract:

```bash
turbo-memory-mcp serve
```

Use the same scope vocabulary everywhere:

Скрізь використовувати однаковий словник scope:

- `project`
- `global`
- `hybrid`

## 7. What I Would Ship In v1 / Що я б ship-нув у v1

1. Claude Code example
2. Codex example
3. Cursor example
4. OpenCode example
5. Antigravity example
6. A smoke-test checklist for each client

1. Приклад для Claude Code
2. Приклад для Codex
3. Приклад для Cursor
4. Приклад для OpenCode
5. Приклад для Antigravity
6. Smoke-test checklist для кожного клієнта
