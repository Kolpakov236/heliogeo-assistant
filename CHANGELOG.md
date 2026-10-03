# Changelog

Все значимые изменения проекта. Формат: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `heliogeo validate` — CLI-команда для проверки JSON по схеме.
- Кастомные валидаторы: `valid_utc`, `valid_kp`, `valid_dst`, `valid_flare_class`.
- PostgreSQL 17 в docker compose (`deploy/compose/`) и цели Makefile `up`, `down`, `migrate`,
  `migrate-down`, `revision`, `test-db`.
- Alembic: `alembic.ini`, `migrations/`, базовая ревизия `0001`.
- `heliogeo.storage`: подключение к БД (`get_engine`, `ping`, `normalize_url`) и ORM-модели.
- Миграция `0002` и модели таблиц `forecast`, `forecast_verification`, `rolling_metrics`
  (прогнозы, их проверка по наблюдениям, скользящие метрики качества).
- Тесты БД (маркер `db`, одноразовая база на тест-сессию) и моки строк прогнозов
  `tests/mocks/forecast.py`.
- Документация: `docs/database.md`.
- Контракты: каталог `contracts/` (facts, lexicon, rules, homogenization), конверт
  `version` / `approved_by` / `approved_at`, пакет `heliogeo.contracts` и валидатор
  `python -m heliogeo.contracts.validate` (`--strict` требует подпись эксперта).
- `contracts/versions.lock.json` и команда `python -m heliogeo.contracts.lock`: содержимое
  контракта нельзя изменить, не повысив `version`.
- Спецификации признаков `contracts/features/specs.yaml`: 40 признаков с обязательной задержкой
  доступности `available_delay` (задержки предварительные).
- Миграция `0003` и модель `observation_daily` (суточные наблюдения; таблица признаков
  `feature_row` пока не создана), в том числе колонки `sw_density_avg`, `sw_sector`,
  `cme_speed_max`, `cme_width_max`, `cme_earth_directed_speed_max`.
- Цели Makefile `contracts` и `contracts-lock`, настройка `CONTRACTS_DIR`.
- Документация `docs/contracts.md`, тесты контрактов и моки `tests/mocks/contracts.py`,
  `tests/mocks/observation.py`.
- Пакеты-заготовки целевой структуры: `mirror`, `facts`, `features`, `forecast`, `planner`,
  `generation`, `validation`, `publishing`, `eval` (пока только описание назначения).
- Контроль границ модулей: import-linter (dev-зависимость) и три правила в `pyproject.toml`
  (`generation` не импортирует `storage` и `extractors`, `validation` — `llm.prompts`,
  `forecast` — `llm` и `generation`); тесты `tests/unit/test_import_boundaries.py`;
  описание — `docs/architecture.md`.
- Модель факта `heliogeo.facts`: `Fact` с видами значений (`FactKind`, включая `distribution`),
  статусами качества (`QCStatus`), происхождением, неопределённостью и политикой отображения;
  инварианты проверяются при создании (имя, время только с поясом, форма значения по виду, связь
  `missing`/`ok` со значением); `display_value` (округление до шага, Kp в трёхбалльной записи,
  язык), `canonical_statement` по шаблону факта, `with_value`, сериализация `to_dict` /
  `from_dict` без потерь.
- Моки фактов `tests/mocks/facts.py` (набор по спокойному дню 2026-09-01) и документация
  `docs/facts.md`.
- Приведение ответа источника к моменту запроса (`heliogeo.extractors.swx.availability`):
  отбрасываются вспышки (по пику) и выбросы позже момента запроса, а также трёхдневный прогноз и
  daypre, выпущенные позже него; отчёт — `SwxExtractor.last_availability` и строка журнала.
- Параметры `--date` и `--no-live` у `heliogeo run`; предупреждение, если дата ближе двух суток и
  `--no-live` не указан (источник тогда подмешивает live-данные, прогон не воспроизводим).
- Документация `docs/data_sources.md`: правила работы с источником, что фильтруется, какие
  ограничения остаются.

### Changed
- `Settings` собирает `database_url` из `POSTGRES_*`, если `DATABASE_URL` не задан. В
  `.env.example` вместо `DATABASE_URL` теперь `POSTGRES_*` (порт 5433). Старую строку
  `DATABASE_URL=postgresql://user:pass@localhost:5432/heliogeo` в своём `.env` нужно удалить.
- pytest по умолчанию пропускает тесты с маркером `db` (`-m 'not db'`); они запускаются
  командой `make test-db`.
- `make lint` дополнительно проверяет контракты и границы модулей (`lint-imports`);
  `make clean` удаляет кэш import-linter.
- Клиент swx передаёт в пути запроса время (`YYYY-MM-DDTHH:MM`, UTC), а не только дату: дата без
  времени читается источником как 12:00 UTC. Время без часового пояса отвергается.
- Клиент swx повторяет запрос (до трёх попыток) только при таймаутах, сетевых сбоях, HTTP 408, 429
  и 5xx; 404 и другие 4xx не повторяются; ответ не в формате JSON — ошибка клиента.
- `SwxExtractor.extract` отбрасывает события «из будущего»: на 12:00 по снимку 2026-09-01 в payload
  остаются одна вспышка из восьми и один выброс из трёх; для целых суток передавайте момент не
  раньше конца окна.
- `solar_wind.density` в payload — среднее за окно (`avg`), а не «последнее или среднее»: в архиве
  «последнее» бывает `null`, и значение менялось по смыслу от дня к дню.
- `run_pipeline` и `build_payload_safe` принимают `query_time` и `no_live`.

## [0.1.0] - 2026-09-22

### Added
- Структура проекта (tasks 0.1–0.2).
- Логирование и конфигурация (task 0.3).
- **Unified JSON Schema v1.0** (task 1.1):
  - `Metadata`, `SolarActivity`, `ActiveRegion`, `Flare`, `CME`.
  - `EnergeticParticles`, `SolarWind`, `Geospace`, `Forecast`, `Images`.
  - `UnifiedPayload` — корневая модель.
- Валидаторы и CLI `validate` (tasks 1.2–1.3).
- Документация схемы (task 1.4).

[Unreleased]: https://example.com/compare/v0.1.0...HEAD
[0.1.0]: https://example.com/releases/tag/v0.1.0
