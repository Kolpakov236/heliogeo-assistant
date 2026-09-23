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
| Рендер | render/ | TXT, PDF, HTML |
| Доставка | delivery/ | Telegram, email, web |
| CLI | cli/ | version, validate, generate |

## Поток данных

1. Extract — extractors читают источники
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

## Ссылки

- [JSON Schema](json_schema.md)
- [Data Sources](data_sources.md)
- [Prompts](prompts.md)
- [Products](products.md)
- [Roadmap](roadmap.md)
- [Runbook](runbook.md)
