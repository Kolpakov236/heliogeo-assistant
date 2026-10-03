# Архитектура Heliogeo Assistant

## Назначение

Автоматизированная система помощи гелиогеофизику. Замещает ручную подготовку:
- ICAO HANDOVER BRIEF (авиация)
- SWPC Forecast Discussion (NOAA)
- Упрощённых версий для пилотов, радиолюбителей

Работает по расписанию (6–12 ч) и по запросу.

## Ключевой принцип

**Один Unified JSON → несколько продуктов.** LLM получает только данные из JSON, grounding-проверка исключает галлюцинации.

## High-level архитектура

InfluxDB + Blobs + DAV → Extractors → Unified JSON → LLM → Products → Delivery
                                                       ↓
                                                   Grounding

## Слои

| Слой | Модуль | Ответственность |
|------|--------|-----------------|
| Ядро | core/ | Config, logging, ошибки |
| Контракт | schema/ | Pydantic-модели UnifiedPayload |
| Извлечение | extractors/ | InfluxDB, DAV, Blobs, Solar |
| Оркестрация | pipeline/ | build_payload, run_pipeline |
| LLM | llm/ | Providers, prompts, grounding |
| Продукты | products/ | ICAO, SWPC, simplified |
| Контракты | contracts/ (данные), heliogeo/contracts/ (код) | Версионируемые YAML под подписью эксперта: реестр фактов, словарь терминов, пороги, спецификации признаков |
| Хранилище | storage/ | Подключение к PostgreSQL, ORM-модели; миграции — migrations/ |
| Зеркало | mirror/ | Локальная копия ответов источника: повторный запрос не идёт в сеть |
| Факты | facts/ | Единственный источник значений: значение + происхождение + качество + политика отображения |
| Признаки | features/ | Признаки для прогнозных моделей строго на момент времени |
| Прогноз | forecast/ | Вероятностные прогнозы, базовые модели, калибровка |
| Планировщик | planner/ | Что сообщить в выпуске и в каком порядке |
| Генерация | generation/ | Текст выпуска по плану и фактам |
| Проверка | validation/ | Проверка сгенерированного текста относительно фактов |
| Публикация | publishing/ | Идемпотентная отправка по каналам, манифест выпуска |
| Оценка | eval/ | Эталонный набор текстов, метрики, сравнение версий |
| Рендер | render/ | TXT, PDF, HTML |
| Доставка | delivery/ | Telegram, email, web |
| CLI | cli/ | version, validate, generate |

Пакеты `mirror/`, `features/`, `forecast/`, `planner/`, `generation/`, `validation/`,
`publishing/` и `eval/` — заготовки целевой структуры: пока в них только описание назначения, код
появляется по мере реализации. В `facts/` уже есть модель факта, правила отображения чисел и
сериализация — см. [Факты](facts.md).

## Границы модулей

Часть правил проверяется автоматически командой `lint-imports` (она входит в `make lint`);
настройки лежат в `pyproject.toml`, раздел `[tool.importlinter]`. Запрещены и косвенные
импорты: цепочка `a → b → c` нарушает запрет `a → c`.

| Модуль | Не может импортировать | Зачем |
|--------|------------------------|-------|
| generation | storage, extractors | Значения приходят только через факты, а не прямо из базы или источника |
| validation | llm.prompts | Проверка не знает, как составлен промпт генератора, и остаётся независимой |
| forecast | llm, generation | Прогноз не зависит от текста |

Практическое следствие: `heliogeo/llm/__init__.py` реэкспортирует промпты, поэтому из
`validation` нужно импортировать конкретные модули (например, `heliogeo.llm.base`), а не пакет
`heliogeo.llm` целиком. Новое правило добавляется в `pyproject.toml`; тест
`tests/unit/test_import_boundaries.py` проверяет, что каждое правило ловит нарушение.

## Поток данных

1. Extract — extractors читают источники и убирают то, чего на момент запроса ещё не было
   (см. [Data Sources](data_sources.md))
2. Build — pipeline/builder.py собирает UnifiedPayload
3. Generate — LLM генерирует текст из JSON + промпта
4. Grounding — проверка: все числа в тексте есть в JSON?
5. Render — конвертация в TXT/PDF
6. Deliver — отправка в Telegram / email / Web

## Ключевые решения

| Решение | Альтернатива | Почему |
|---------|--------------|--------|
| Единый JSON | Сырые данные в LLM | Контракт, тестируемость |
| Pydantic | dict | Валидация, автодок |
| Grounding regex | LLM-проверка | Детерминизм |
| YAML | Python-константы | Менять без пересборки |
| Poetry | pip | Lock, группы |

## Технологии

Python 3.11+, Pydantic 2.x, Typer 0.12, influxdb-client 1.x, pytest 8.x, ruff 0.4, mypy strict.
PostgreSQL 17 (docker compose для разработки), SQLAlchemy 2.x, Alembic, psycopg 3.

## Ограничения

- LLM не имеет доступа к интернету — только JSON
- Grounding обязателен перед доставкой
- Enlil — анимация, численная интерпретация — TODO
- VLM для изображений — не используется

## Roadmap

| Фаза | Статус |
|------|--------|
| MVP (схема + CLI) | ✅ |
| Extractors (InfluxDB) | 🟡 |
| Solar activity | ⏳ |
| LLM + grounding | ⏳ |
| Render + delivery | ⏳ |
| Планировщик | ⏳ |
| Хранилище (PostgreSQL, миграции) | 🟡 |
| Факты (модель, отображение, сериализация) | 🟡 |

## Ссылки

- [JSON Schema](json_schema.md)
- [База данных и миграции](database.md)
- [Контракты](contracts.md)
- [Факты](facts.md)
- [Data Sources](data_sources.md)
- [Prompts](prompts.md)
- [Products](products.md)
- [Roadmap](roadmap.md)
- [Runbook](runbook.md)
