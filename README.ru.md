# Turbo Quant Memory for AI Agents

![Заглавная иллюстрация Turbo Quant Memory](assets/readme-hero-ru.svg?v=20260328b)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Другие языки: [English](README.md) | [Украинский](README.uk.md)

Turbo Quant Memory — это слой памяти, который превращает AI-агента из “умного, но забывчивого помощника” в надёжного участника команды.

Если вы работаете с Claude Code, Codex, Cursor, OpenCode, Gemini CLI или любым MCP-клиентом, это способ сохранить и масштабировать знания команды между задачами.

## Почему Это Важно

Большинство AI-процессов ломаются в одном месте: память.

- Важные выводы теряются в истории чатов.
- Каждая новая задача стартует почти с нуля.
- Команда снова и снова объясняет одни и те же вещи.

Turbo Quant Memory делает знания постоянными, поисковыми и переиспользуемыми.

## Почему Команды Выбирают Turbo Quant Memory

| Типичный AI-процесс | С Turbo Quant Memory |
|---|---|
| Агент забывает контекст между сессиями | Агент продолжает работу с уже сохранённых знаний |
| Решения спрятаны в старых тредах | Решения становятся заметками, которые легко найти |
| Знания держатся на отдельных людях | Знания становятся общим активом команды |
| Контекст тратится на повторное чтение | Больше бюджета остаётся на рассуждение |

## Главная Обещанная Ценность

Ваши агенты перестают быть “временными чат-ассистентами” и начинают работать как полноценные члены команды.

## Что Делает Этот Проект Особенным

- Local-first подход: память остаётся под вашим контролем.
- Один слой памяти для многих клиентов.
- Фокус на реальной разработке: решения, паттерны, handoff-и.
- Прозрачность: знания структурированы и проверяемы.

## Быстрый Старт

Используйте этот сценарий на 60 секунд:

1. Установите один раз:
```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.3.0
```

2. Добавьте MCP-сервер `tqmemory` в клиент (клиент будет запускать его автоматически):

```bash
# Codex
codex mcp add tqmemory -- turbo-memory-mcp serve

# Claude Code (project scope)
claude mcp add --scope project tqmemory -- turbo-memory-mcp serve
```

3. Перезапустите клиент и вызовите любой инструмент `tqmemory`.

Нужны Cursor, OpenCode или Antigravity? Используйте готовые конфиги из [CLIENT_INTEGRATIONS.ru.md](CLIENT_INTEGRATIONS.ru.md).

## Для Кого Это

- инженерные команды с AI-first процессами
- соло-разработчики с несколькими агентами
- продуктовые команды, которым нужна стабильность AI-исполнения
- все, кто устал ежедневно повторять контекст

## Почему Нужно Выбрать Именно Это

Turbo Quant Memory даёт:

- быстрый старт для каждой новой задачи
- меньше повторных ошибок
- непрерывность между сессиями
- более высокую отдачу от каждого запуска агента

## Преимущество, Подтверждённое Бенчмарками И Экономией Денег

![Сводка бенчмарков](benchmarks/summary-ru.svg)

На реальном корпусе этого репозитория компактный memory-путь показывает сильную экономию, которая напрямую снижает расходы на модель:

- только `semantic_search`: в среднем **-63.96% байтов** в модель
- `semantic_search + hydrate(top1)`: в среднем **-44.1% байтов**
- средняя задержка `semantic_search`: **68.13 мс**
- средняя задержка `hydrate`: **41.63 мс**

Почему это практическое преимущество:

- меньше повторного чтения означает меньше платных входных токенов
- более низкий токен-объём означает более низкую стоимость каждой задачи
- контекстный бюджет идёт на рассуждение, а не на повторную загрузку файлов

## Что Добавлено В v0.3.0

- Версионированные index manifest-ы автоматически пересобирают производные индексы после format-changing апдейта.
- `server_info()` теперь показывает постоянную usage/savings telemetry отдельно от memory scope.
- Задайте `TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD`, если хотите видеть примерную экономию в USD.
- Retrieval-ответы могут время от времени показывать короткие savings milestone без засорения памяти.

## Где Посмотреть Детали

- Интеграции с клиентами: [CLIENT_INTEGRATIONS.ru.md](CLIENT_INTEGRATIONS.ru.md)
- Техническая спецификация: [TECHNICAL_SPEC.ru.md](TECHNICAL_SPEC.ru.md)
- Стратегия памяти: [MEMORY_STRATEGY.ru.md](MEMORY_STRATEGY.ru.md)
- Бенчмарки: [benchmarks/latest.ru.md](benchmarks/latest.ru.md)
