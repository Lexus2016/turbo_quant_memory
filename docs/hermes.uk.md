# 🛰️ Специфіка платформ: Hermes Agent

[Hermes](https://github.com/nicepkg/hermes) запускає MCP-сервери через systemd-керований шлюз — це інший підхід, ніж Claude Code або Cursor.

### Встановлення

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory
```

Додай до `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  tqmemory:
    command: turbo-memory-mcp
    args: ["serve"]
    enabled: true
```

Перезапусти шлюз:

```bash
systemctl --user restart hermes-gateway
```

### Вирішення проблем з MCP-таймаутами

Якщо MCP-інструменти видають таймаут "MCP call timed out after 120.0s" — скоріше за все, блокування демона зависло після попереднього крашу або сну хоста. Відновлення:

```bash
# 1. Завершити всі процеси демона
pkill -f turbo-memory-mcp

# 2. Видалити завислий lock-файл
rm -f ~/.turbo-quant-memory/.daemon.lock

# 3. Перевірити та застосувати міграції
turbo-memory-mcp migrate --status
turbo-memory-mcp migrate --apply

# 4. Швидка діагностика
turbo-memory-mcp doctor

# 5. Перезапустити шлюз
systemctl --user restart hermes-gateway

# 6. Зачекати 30-60с для повторного підключення MCP
```

#### "memory server busy" (v0.27.0+)

Інший тип збою: `memory server busy: 'index_paths' holds the dispatch lock
(waited 30s); retry this call later`. Це **не** завислий lock — це означає, що
паралельна операція справді ще виконується й тримає single-writer dispatch-лок,
а твій виклик здався замість того, щоб чекати до власного тайм-ауту MCP-хоста
(ті самі жорсткі 420-600s до v0.27.0, які мовчки губили записи в пам'ять).
Помилка називає інструмент, що блокує; той самий рядок пишеться у stderr із
префіксом `[tqmemory]`.

Просто повтори виклик — нічого не було записано, тож дубль неможливий. Якщо
розгортання легітимно тримає лок довше за дефолтні 30s (великий `index_paths`
по чималому репозиторію, холодний ембеддинг-бекенд), підніми межу або взагалі
відмовся від неї:

```bash
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=90   # секунди; за замовчуванням 30
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=0    # <= 0: чекати без обмеження (поведінка до 0.27.0)
```

Тримай межу нижче за власний `RPC_TIMEOUT_SECONDS` проксі (120s), щоб явна
помилка "busy" вигравала в непрозорого RPC-тайм-ауту.

### Автоматичні міграції при старті

Встанови `TQMEMORY_MIGRATE_ON_STARTUP=1` у середовищі, щоб сервер автоматично застосовував очікувані міграції (з роллінг-знімком) при старті у ролі primary або standalone:

```yaml
mcp_servers:
  tqmemory:
    command: turbo-memory-mcp
    args: ["serve"]
    enabled: true
    env:
      TQMEMORY_MIGRATE_ON_STARTUP: "1"
```

Результат авто-міграції видно у відповіді `health()` у полі `migration_auto_result`.

### Типові проблеми з Hermes

| Симптом | Причина | Виправлення |
|---------|---------|-------------|
| MCP-таймаут | Завислий `.daemon.lock` | `rm -f ~/.turbo-quant-memory/.daemon.lock` |
| Кілька демонів | Краш залишив сироти | `pkill -f turbo-memory-mcp` |
| Інструменти повертають помилки | Очікуються міграції схеми | `turbo-memory-mcp migrate --apply` |
| Шлюз не завантажує MCP | Помилка синтаксису config | Перевірити `config.yaml` |
| Тихий збій старту | Немає видимості ролі демона | Перевірити stderr: `[tqmemory] role=...` |
