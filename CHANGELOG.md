# Changelog

Все значимые изменения проекта. Формат: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `heliogeo validate` — CLI-команда для проверки JSON по схеме.
- Кастомные валидаторы: `valid_utc`, `valid_kp`, `valid_dst`, `valid_flare_class`.
- PostgreSQL 17 в docker compose (`deploy/compose/`) и цели Makefile `up`, `down`, `migrate`,
  `migrate-down`, `revision`, `test-db` (задача B0-04).
- Alembic: `alembic.ini`, `migrations/`, базовая ревизия `0001` (B0-04).
- `heliogeo.storage`: подключение к БД (`get_engine`, `ping`, `normalize_url`) и ORM-модели
  (B0-04, B0-06).
- Миграция `0002` и модели таблиц `forecast`, `forecast_verification`, `rolling_metrics` (B0-06).
- Тесты БД (маркер `db`, одноразовая база на тест-сессию) и моки строк прогнозов
  `tests/mocks/forecast.py`.
- Документация: `docs/database.md`.
- Контракты (B0-02): каталог `contracts/` (facts, lexicon, rules, homogenization), конверт
  `version` / `approved_by` / `approved_at`, пакет `heliogeo.contracts` и валидатор
  `python -m heliogeo.contracts.validate` (`--strict` требует подпись эксперта).
- `contracts/versions.lock.json` и команда `python -m heliogeo.contracts.lock`: содержимое
  контракта нельзя изменить, не повысив `version`.
- Спецификации признаков `contracts/features/specs.yaml` (B3-01): 40 признаков с обязательной
  задержкой доступности `available_delay` (задержки предварительные).
- Миграция `0003` и модель `observation_daily` (часть B0-05, `feature_row` не создана); колонки
  `sw_density_avg`, `sw_sector`, `cme_speed_max`, `cme_width_max`, `cme_earth_directed_speed_max`.
- Цели Makefile `contracts` и `contracts-lock`, настройка `CONTRACTS_DIR`.
- Документация `docs/contracts.md`, тесты контрактов и моки `tests/mocks/contracts.py`,
  `tests/mocks/observation.py`.

### Changed
- `Settings` собирает `database_url` из `POSTGRES_*`, если `DATABASE_URL` не задан. В
  `.env.example` вместо `DATABASE_URL` теперь `POSTGRES_*` (порт 5433). Старую строку
  `DATABASE_URL=postgresql://user:pass@localhost:5432/heliogeo` в своём `.env` нужно удалить.
- pytest по умолчанию пропускает тесты с маркером `db` (`-m 'not db'`); они запускаются
  командой `make test-db`.
- `make lint` дополнительно проверяет контракты.

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
