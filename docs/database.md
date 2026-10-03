# База данных и миграции

PostgreSQL 17, доступ через SQLAlchemy 2.x (драйвер psycopg 3), миграции — Alembic. Локально БД
поднимается в docker compose. Схема таблиц — ЭТАП1 §6, описание — в конце документа.

## Быстрый старт

```bash
cp .env.example .env   # значения по умолчанию подходят для локальной БД
make install
make up                # PostgreSQL в docker compose, ждёт healthcheck
make migrate           # alembic upgrade head
```

БД слушает `127.0.0.1:5433` (не 5432: на 5432 часто уже стоит локальный PostgreSQL); логин,
пароль и имя базы — `heliogeo`. Данные лежат в томе `heliogeo_pgdata` и переживают `make down`.
Чтобы начать с чистой БД, удалите том:
`docker compose --project-directory . -f deploy/compose/docker-compose.yml down -v`.

## Что где

| Путь | Назначение |
|---|---|
| `deploy/compose/docker-compose.yml` | PostgreSQL для разработки |
| `alembic.ini`, `migrations/` | конфигурация Alembic, `env.py`, ревизии в `migrations/versions/` |
| `src/heliogeo/storage/db.py` | `normalize_url`, `get_engine`, `ping` |
| `src/heliogeo/storage/models/` | ORM-модели: `Base` и `*Record` |
| `tests/integration/`, `tests/mocks/` | тесты с БД и фабрики тестовых данных |

## Подключение

Приложение и Alembic берут настройки из `.env` / переменных окружения (`core/config.py`):

| Переменная | По умолчанию | Примечание |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `heliogeo` | те же значения читает compose |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `localhost`, `5433` | `POSTGRES_PORT` задаёт и порт на хосте в compose |
| `DATABASE_URL` | не задан | полный DSN; если задан, приоритетнее `POSTGRES_*` |

Без `DATABASE_URL` DSN собирается из `POSTGRES_*` (спецсимволы в пароле экранируются). URL вида
`postgresql://…` автоматически превращается в `postgresql+psycopg://…` (`storage.db.normalize_url`).

```python
from heliogeo.storage.db import ping

ping()  # версия сервера; исключение, если БД недоступна
```

## Команды Makefile

| Цель | Что делает |
|---|---|
| `make up` / `make down` | поднять БД и дождаться healthcheck / остановить (данные сохраняются) |
| `make migrate` | применить миграции (`alembic upgrade head`) |
| `make migrate-down` | откатить одну ревизию (`alembic downgrade -1`) |
| `make revision m="описание" id=0003` | создать пустую ревизию с заданным номером |
| `make test-db` | тесты, которым нужна БД (`pytest -m db`) |

## Миграции

- **Нумерация.** Файлы `NNNN_slug.py` и `revision = "NNNN"`: `0001_baseline.py`,
  `0002_forecast_tables.py`. Следующий номер задаёт `make revision … id=NNNN`.
- **Одна «голова».** История линейная. Если две ветки добавили ревизии параллельно, тот, кто
  сливает позже, меняет номер и `down_revision` на последнюю слитую; тест
  `tests/unit/test_migration_graph.py` не пропустит две «головы».
- **Руками, без autogenerate.** Ревизии пишутся на `op.*` и ревьюятся против спецификации.
  Совпадение моделей и схемы проверяют тест `test_models_match_migrated_schema` и команда
  `poetry run alembic check`.
- **Имена ограничений** задаются явно через `op.f("…")` по соглашению из
  `storage/models/base.py` (`pk_`, `uq_`, `ck_`, `fk_`, `ix_`).
- **Ревизии не импортируют код приложения**: миграция должна воспроизводиться, даже когда модели
  изменятся.
- **`downgrade`** нужен для разработки и тестов; в проде миграции только накатываются вперёд.
- **Без `CREATE EXTENSION`.** Таблицам этапа 1 расширения не нужны, а в управляемом PostgreSQL
  Yandex Cloud они включаются параметрами кластера.

Как добавить таблицу: (1) модель в `src/heliogeo/storage/models/` и импорт в `models/__init__.py`
(иначе её не увидит Alembic); (2) `make revision m="…" id=NNNN` и ручной `upgrade`/`downgrade`;
(3) тесты схемы и поведения, моки — в `tests/mocks/`; (4) раздел в этом документе.

## Тесты

- `make test` — обычный прогон. Тесты с маркером `db` в нём пропускаются (`-m 'not db'` в
  `pyproject.toml`), БД не нужна.
- `make test-db` — тесты с БД, нужен `make up`. Рабочую базу они **не трогают**: на сервере
  создаётся одноразовая БД `heliogeo_test_<hex>`, в ней накатываются миграции, после тестов она
  удаляется (нужно право создавать базы — в compose оно есть). Сервер берётся из
  `TEST_DATABASE_URL`, если он задан, иначе из `DATABASE_URL` / `POSTGRES_*`.
- Без БД проверяются: нормализация URL и сборка DSN, граф миграций (одна «голова»), структура и
  DDL моделей, `ping`/`get_engine` на подменённых объектах (`unittest.mock`).
- Моки `tests/mocks/forecast.py`: `make_forecast`, `make_issue_forecasts`, `make_verification`,
  `make_rolling_metrics` — правдоподобные строки таблиц, любое поле переопределяется:
  `make_forecast(source="swpc", horizon_days=2)`. Метрики верификации в них согласованы с
  распределением прогноза, но считаются упрощённо (эталонные метрики — задача B4-06).
- Моки `tests/mocks/observation.py`: `make_observation_daily` (спокойные сутки 2026-09-01),
  `make_storm_observation_daily` (буря 2024-05-11) и `make_empty_observation_daily` (сутки без
  данных). Значения взяты из `fixtures/swx_snapshot_*.json`, и тест сверяет их с этими снимками.

## Модель данных: прогнозы и верификация (миграция 0002, B0-06)

ORM-модели называются `ForecastRecord`, `ForecastVerificationRecord`, `RollingMetricsRecord`,
чтобы не путать их с Pydantic-моделью `heliogeo.schema.Forecast` (прогноз из SWX API) и с будущими
доменными объектами прогнозов.

### `forecast` — прогноз

Одна строка — прогноз одного источника на одно целевое окно.

| Поле | Тип | Смысл |
|---|---|---|
| `id` | BIGSERIAL, PK | |
| `issued_at` | TIMESTAMPTZ | когда выпущен |
| `quantity` | TEXT | `geomag_level`, `flare_m`, `flare_x` |
| `horizon_days` | SMALLINT | 1, 2 или 3 (CHECK) |
| `target_from`, `target_to` | TIMESTAMPTZ | целевое окно; `target_to > target_from` (CHECK) |
| `source` | TEXT | `ml`, `persistence`, `recurrence`, `climatology`, `swpc` |
| `distribution` | JSONB | распределение по уровням, `{"quiet": 0.4, "G1": 0.2, …}`; JSON-объект (CHECK) |
| `point_estimate` | TEXT | самый вероятный уровень |
| `model_version`, `calibrator_v` | TEXT | версии модели и калибратора |
| `features_t`, `feature_set_v` | TIMESTAMPTZ, TEXT | строка признаков (`feature_row`, PK `(t, feature_set_v)`); внешнего ключа нет |

`UNIQUE (issued_at, quantity, horizon_days, source)`, индекс `(target_from, quantity)`.

### `forecast_verification` — сверка с наблюдением (контур B)

| Поле | Тип | Смысл |
|---|---|---|
| `forecast_id` | BIGINT, FK → `forecast(id)` | |
| `verified_at` | TIMESTAMPTZ, по умолчанию `now()` | |
| `observed_level`, `observed_value` | TEXT, DOUBLE PRECISION | что наблюдалось |
| `obs_source` | TEXT | по какому источнику проверяли |
| `obs_is_final` | BOOLEAN | окончательные данные (`true`) или оперативные (`false`) |
| `brier`, `rps`, `log_score` | DOUBLE PRECISION | оценки прогноза |
| `hit` | BOOLEAN | совпал ли самый вероятный уровень с наблюдением |

PK `(forecast_id, obs_is_final)`: прогноз верифицируется дважды — по оперативным и по окончательным
данным, — и обе оценки хранятся отдельными строками (ЭТАП1 §3.6).

### `rolling_metrics` — скользящие метрики качества

PK `(computed_at, quantity, horizon_days, source, window_days)`; `horizon_days` 1–3, `window_days`
> 0, `n` ≥ 0 (CHECK). Метрики: `bss`, `rps_mean`, `reliability`, `resolution`, `sharpness`,
`tss_g1`, `tss_g2` (все DOUBLE PRECISION, могут быть NULL).

### Отличия от ЭТАП1 §6

| Что | Зачем |
|---|---|
| PK `forecast_verification` — `(forecast_id, obs_is_final)`, а не `(forecast_id)` | иначе вторую оценку негде хранить (ЭТАП1 §3.6) |
| `forecast.feature_set_v` | одного `features_t` мало, чтобы найти строку `feature_row`; нужно и для срезов по эпохам прибора (УСТ §2.5) |
| `rolling_metrics.resolution`, `sharpness` | панель качества (УСТ §6.2–6.3, §6.8) |
| CHECK-ограничения | целостность: горизонт, окно, JSON-объект, `n`, `window_days` |

### Договорённости

- Значения `quantity`, `source` и ключи `distribution` / `observed_level` схемой не ограничены
  (TEXT/JSONB): их задаёт код прогнозов. По УСТ §1.2–1.3 модель `ml` появится в двух вариантах
  (автономная и расширенная) и добавится ансамбль; раз `source` входит в UNIQUE, варианты должны
  называться по-разному.
- Уровни в `distribution`: в ЭТАП1 §2.4 их шесть (`quiet`, `unsettled`, `active`, `G1`, `G2`,
  `G3+`), в примере §7 — пять (последний `G2+`). В моках используются шесть; окончательный
  словарь фиксируется вместе с моделью M1 (B4-10).

## Модель данных: суточные наблюдения (миграция 0003, часть B0-05)

Из задачи B0-05 сделана только таблица `observation_daily` (модель `ObservationDailyRecord`, мок
`tests/mocks/observation.py`); `feature_row` и отпечаток прибора в неё не входят (см. ниже).

Одна строка — одни сутки (`d`, первичный ключ). **NULL значит «нет данных»**: отсутствие не
подменяется нулём (РУК §2.2), поэтому всё, кроме даты, ключа сырья и времени загрузки, допускает
NULL.

| Группа | Колонки |
|---|---|
| Геомагнитная обстановка | `kp_max`, `kp_values` (до 8 трёхчасовых значений), `kp_source`, `dst_min`, `dst_source` |
| Солнечный ветер | `sw_speed_min`, `sw_speed_avg`, `sw_speed_max`, `sw_bt`, `sw_bz_min`, `sw_density_avg`\*, `sw_sector`\*, `sw_source` |
| Вспышки | `flare_max_class`, `flare_counts` (JSONB, `{"B": 5, "C": 3, "M": 0, "X": 0}`) |
| Активные области | `ar_count`, `ar_total_area`, `ar_has_delta`, `ar_summary` (JSONB: классы McIntosh и рост `new`/`continuing`; структуру задаст B1-14) |
| Корональные выбросы | `cme_count`, `cme_earth_directed`, `cme_direction_unknown`, `cme_speed_max`\*, `cme_width_max`\*, `cme_earth_directed_speed_max`\* |
| Служебные | `qc` (JSONB), `raw_s3_key` (обязателен: сырьё сохраняется раньше разбора), `ingested_at` (по умолчанию `now()`) |

\* — колонки, которых нет в ЭТАП1 §6. Они нужны признакам `sw_density_avg`, `sw_sector`,
`cme_speed_max_24h`, `cme_width_max_24h` и `cme_earth_directed_speed_max_24h` из
`contracts/features/specs.yaml` ([contracts.md](contracts.md)). Источники в ответе SWX API:
`solar_wind.density.avg` (`latest` бывает пуст), `solar_wind.imf.sector` (`toward` или `away`),
`speed` и `width` выбросов. `cme_earth_directed_speed_max` равна NULL, если выбросов к Земле не
было.

Ограничения (CHECK):
- `kp_values` не длиннее 8: защита от «двойного Kp» (в `solar_wind.kp_obs` 24 значения, в
  `geomag.kp_obs` — 8, РУК §2.4, ловушка 3);
- счётчики не отрицательны, а `cme_earth_directed + cme_direction_unknown <= cme_count`;
- `flare_counts`, `ar_summary` и `qc` — JSON-объекты.

## Что осталось от B0-05 и соседних задач

- B0-05: таблица `feature_row` и отпечаток прибора (`fingerprint_key`, УСТ §2.3) в `feature_row` и
  `observation_daily`. В ЭТАП1 §6 его нет, но он нужен для метрик в разрезе эпох.
- B0-07: представление `verbal_calibration` идёт после `0003`; оно должно брать одну оценку на
  прогноз (окончательную, иначе оперативную) и сравнивать уровни по порядковому номеру, а не как
  строки (`'quiet' >= 'G1'` в SQL истинно).
