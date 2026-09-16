# 🛰️ Специфика платформ: Hermes Agent

[Hermes](https://github.com/nicepkg/hermes) запускает MCP-серверы через systemd-управляемый шлюз — это другой подход, чем Claude Code или Cursor.

### Установка

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory
```

Добавь в `~/.hermes/config.yaml`:

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

### Решение проблем с MCP-таймаутами

Если MCP-инструменты выдают таймаут "MCP call timed out after 120.0s" — скорее всего, блокировка демона зависла после предыдущего круша или сна хоста. Восстановление:

```bash
# 1. Завершить все процессы демона
pkill -f turbo-memory-mcp

# 2. Удалить зависший lock-файл
rm -f ~/.turbo-quant-memory/.daemon.lock

# 3. Проверить и применить миграции
turbo-memory-mcp migrate --status
turbo-memory-mcp migrate --apply

# 4. Быстрая диагностика
turbo-memory-mcp doctor

# 5. Перезапустить шлюз
systemctl --user restart hermes-gateway

# 6. Подождать 30-60с для повторного подключения MCP
```

#### "memory server busy" (v0.27.0+)

Другой тип сбоя: `memory server busy: 'index_paths' holds the dispatch lock
(waited 30s); retry this call later`. Это **не** зависший lock — это значит, что
параллельная операция действительно ещё выполняется и держит single-writer
dispatch-лок, а твой вызов сдался вместо ожидания до собственного тайм-аута
MCP-хоста (те самые жёсткие 420-600s до v0.27.0, которые молча теряли записи в
память). Ошибка называет блокирующий инструмент; та же строка пишется в stderr с
префиксом `[tqmemory]`.

Просто повтори вызов — ничего не было записано, поэтому дубль невозможен. Если
развёртывание легитимно держит лок дольше дефолтных 30s (большой `index_paths`
по крупному репозиторию, холодный embedding-бэкенд), подними границу или
откажись от неё совсем:

```bash
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=90   # секунды; по умолчанию 30
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=0    # <= 0: ждать без ограничения (поведение до 0.27.0)
```

Держи границу ниже собственного `RPC_TIMEOUT_SECONDS` прокси (120s), чтобы явная
ошибка "busy" выигрывала у непрозрачного RPC-тайм-аута.

### Автоматические миграции при старте

Установи `TQMEMORY_MIGRATE_ON_STARTUP=1` в окружении, чтобы сервер автоматически применял ожидающие миграции (с rolling-снимком) при старте в роли primary или standalone:

```yaml
mcp_servers:
  tqmemory:
    command: turbo-memory-mcp
    args: ["serve"]
    enabled: true
    env:
      TQMEMORY_MIGRATE_ON_STARTUP: "1"
```

Результат авто-миграции виден в ответе `health()` в поле `migration_auto_result`.

### Типичные проблемы с Hermes

| Симптом | Причина | Исправление |
|---------|---------|-------------|
| MCP-таймаут | Зависший `.daemon.lock` | `rm -f ~/.turbo-quant-memory/.daemon.lock` |
| Несколько демонов | Краш оставил сироты | `pkill -f turbo-memory-mcp` |
| Инструменты возвращают ошибки | Ожидаются миграции схемы | `turbo-memory-mcp migrate --apply` |
| Шлюз не загружает MCP | Ошибка синтаксиса config | Проверить `config.yaml` |
| Тихий сбой старта | Нет видимости роли демона | Проверить stderr: `[tqmemory] role=...` |
