# Turbo Quant Memory for AI Agents

![Титульна ілюстрація Turbo Quant Memory](assets/readme-hero-uk.svg?v=20260328b)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Інші мови: [English](README.md) | [Russian](README.ru.md)

Turbo Quant Memory — це шар пам’яті, який перетворює AI-агента з “розумного, але короткозорого помічника” на стабільного члена команди.

Якщо ви працюєте з Claude Code, Codex, Cursor, OpenCode, Gemini CLI або будь-яким MCP-клієнтом, саме так зберігається інституційна пам’ять вашого продукту.

## Чому Це Важливо

Більшість AI-флоу ламаються в одному місці: пам’ять.

- Корисні висновки губляться в чатах.
- Кожна нова задача стартує майже з нуля.
- Команда знову і знову пояснює одне й те саме.

Turbo Quant Memory робить знання постійними, пошуковими та придатними до повторного використання.

## Чому Команди Обирають Turbo Quant Memory

| Типовий AI-процес | З Turbo Quant Memory |
|---|---|
| Агент забуває контекст між сесіями | Агент продовжує роботу з уже збережених знань |
| Рішення заховані в старих тредах | Рішення стають нотатками, які легко знайти |
| Знання тримається на окремих людях | Знання стає спільним активом команди |
| Контекст витрачається на повторне читання | Більше бюджету залишається на міркування |

## Головна Обіцянка

Ваші агенти перестають бути “тимчасовими чат-асистентами” і починають працювати як повноцінні учасники команди.

## Що Робить Його Особливим

- Local-first підхід: пам’ять під вашим контролем.
- Один шар пам’яті для багатьох клієнтів.
- Орієнтація на реальну розробку: рішення, патерни, handoff-и.
- Прозорість: знання структуроване й перевіряється.

## Швидкий Старт

Встановлення:

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4
turbo-memory-mcp serve
```

Підключіть у клієнті MCP-сервер `tqmemory` з командою `turbo-memory-mcp serve`.

Конфіги для різних клієнтів: [CLIENT_INTEGRATIONS.uk.md](CLIENT_INTEGRATIONS.uk.md)

## Для Кого Це

- інженерні команди, що будують AI-first процеси
- соло-розробники з кількома агентами
- продуктові команди, яким потрібна стабільна якість AI-виконання
- усі, хто втомився повторювати контекст щодня

## Чому Треба Обрати Саме Це

Turbo Quant Memory дає:

- швидший старт кожної нової задачі
- менше повторних помилок
- безперервність між сесіями
- вищу віддачу від кожного запуску агента

## Де Подивитися Деталі

- Інтеграції з клієнтами: [CLIENT_INTEGRATIONS.uk.md](CLIENT_INTEGRATIONS.uk.md)
- Технічна специфікація: [TECHNICAL_SPEC.uk.md](TECHNICAL_SPEC.uk.md)
- Стратегія пам’яті: [MEMORY_STRATEGY.uk.md](MEMORY_STRATEGY.uk.md)
- Бенчмарки: [benchmarks/latest.uk.md](benchmarks/latest.uk.md)
