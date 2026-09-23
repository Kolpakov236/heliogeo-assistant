# Changelog

Все значимые изменения проекта. Формат: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `heliogeo validate` — CLI-команда для проверки JSON по схеме.
- Кастомные валидаторы: `valid_utc`, `valid_kp`, `valid_dst`, `valid_flare_class`.

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
