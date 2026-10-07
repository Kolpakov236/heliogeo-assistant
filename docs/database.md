# База данных и миграции

PostgreSQL 17, доступ через SQLAlchemy 2.x (драйвер psycopg 3), миграции — Alembic. Локально БД
поднимается в docker compose. Описание таблиц — в конце документа; в облаке то же состояние
хранит YDB — см. раздел «Облачный контур: YDB».

## Что хранится

- **Суточные наблюдения** (`observation_daily`): сводка того, что произошло на Солнце и в
  околоземном пространстве за сутки, из ответа SWX API.
- **Прогнозы** (`forecast`): вероятностные прогнозы на 1–3 суток вперёд от нескольких источников.
- **Проверка прогнозов**: когда наступает целевое окно, прогноз сверяют с наблюдением
  (`forecast_verification`), а по результатам считают скользящие метрики качества
  (`rolling_metrics`).

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
| `make revision m="описание" id=0004` | создать пустую ревизию с заданным номером |
| `make test-db` | тесты, которым нужна БД (`pytest -m db`) |

## Миграции

- **Нумерация.** Файлы `NNNN_slug.py` и `revision = "NNNN"`: `0001_baseline.py`,
  `0002_forecast_tables.py`, `0003_observation_daily.py`. Следующий номер задаёт
  `make revision … id=NNNN`.
- **Одна «голова».** История линейная. Если две ветки добавили ревизии параллельно, тот, кто
  сливает позже, меняет номер и `down_revision` на последнюю слитую; тест
  `tests/unit/test_migration_graph.py` не пропустит две «головы».
- **Руками, без autogenerate.** Ревизии пишутся на `op.*` и проверяются на ревью построчно.
  Совпадение моделей и схемы проверяют тест `test_models_match_migrated_schema` и команда
  `poetry run alembic check`.
- **Имена ограничений** задаются явно через `op.f("…")` по соглашению из
  `storage/models/base.py` (`pk_`, `uq_`, `ck_`, `fk_`, `ix_`).
- **Ревизии не импортируют код приложения**: миграция должна воспроизводиться, даже когда модели
  изменятся.
- **`downgrade`** нужен для разработки и тестов; в проде миграции только накатываются вперёд.
- **Без `CREATE EXTENSION`.** Текущим таблицам расширения не нужны, а в управляемых облачных
  PostgreSQL (например, в Yandex Cloud) расширения включаются настройками кластера, а не командой
  SQL.

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
  распределением прогноза, но считаются упрощённо: настоящий расчёт метрик появится вместе с
  модулем прогнозов.
- Моки `tests/mocks/observation.py`: `make_observation_daily` (спокойные сутки 2026-09-01),
  `make_storm_observation_daily` (буря 2024-05-11) и `make_empty_observation_daily` (сутки без
  данных). Значения взяты из `fixtures/swx_snapshot_*.json`, и тест сверяет их с этими снимками.

## Модель данных: прогнозы и верификация (миграция 0002)

ORM-модели называются `ForecastRecord`, `ForecastVerificationRecord`, `RollingMetricsRecord`,
чтобы не путать их с Pydantic-моделью `heliogeo.schema.Forecast` (прогноз из SWX API) и с будущими
доменными объектами прогнозов.

### `forecast` — прогноз

Одна строка — прогноз одного источника на одно целевое окно.

| Поле | Тип | Смысл |
|---|---|---|
| `id` | BIGSERIAL, PK | |
| `issued_at` | TIMESTAMPTZ | когда выпущен |
| `quantity` | TEXT | что прогнозируется: `geomag_level` — максимальный уровень геомагнитной активности за сутки; `flare_m`, `flare_x` — вспышка класса M / X за 24 часа |
| `horizon_days` | SMALLINT | на сколько суток вперёд: 1, 2 или 3 (CHECK) |
| `target_from`, `target_to` | TIMESTAMPTZ | целевое окно, на которое дан прогноз; `target_to > target_from` (CHECK) |
| `source` | TEXT | кто сделал прогноз (см. ниже) |
| `distribution` | JSONB | распределение вероятностей по уровням, `{"quiet": 0.4, "G1": 0.2, …}`; JSON-объект (CHECK) |
| `point_estimate` | TEXT | самый вероятный уровень |
| `model_version`, `calibrator_v` | TEXT | версии модели и калибратора (слоя, приводящего вероятности модели к наблюдаемым частотам) |
| `features_t`, `feature_set_v` | TIMESTAMPTZ, TEXT | какая строка признаков использована: момент и версия набора признаков (`feature_set_v` — версия `contracts/features/specs.yaml`, см. [contracts.md](contracts.md)); таблица признаков `feature_row` пока не создана, внешнего ключа нет |

`UNIQUE (issued_at, quantity, horizon_days, source)`, индекс `(target_from, quantity)`.

Источники прогноза (`source`): `ml` — собственная модель; `persistence` — «завтра как сегодня»;
`recurrence` — «как 27 суток назад» (оборот Солнца, на нём повторяются потоки из корональных дыр);
`climatology` — историческая частота; `swpc` — прогноз Центра прогнозирования космической погоды
NOAA (SWPC), который приходит в ответе SWX API.

Уровни геомагнитной активности в `distribution` и `observed_level`: `quiet` (спокойно),
`unsettled` (слабо возмущено), `active` (возмущено), `G1`, `G2`, `G3+` — магнитные бури по шкале
NOAA от слабой (G1) до сильной и сильнее (G3 и выше).

### `forecast_verification` — сверка с наблюдением

| Поле | Тип | Смысл |
|---|---|---|
| `forecast_id` | BIGINT, FK → `forecast(id)` | |
| `verified_at` | TIMESTAMPTZ, по умолчанию `now()` | |
| `observed_level`, `observed_value` | TEXT, DOUBLE PRECISION | что наблюдалось (уровень и числовое значение, например Kp) |
| `obs_source` | TEXT | по какому источнику проверяли |
| `obs_is_final` | BOOLEAN | окончательные данные (`true`) или оперативные (`false`) |
| `brier`, `rps`, `log_score` | DOUBLE PRECISION | оценки прогноза: оценка Брайера, ранжированный вероятностный балл (учитывает порядок уровней: ошибка на два уровня хуже, чем на один), логарифмическая оценка; меньше — лучше |
| `hit` | BOOLEAN | совпал ли самый вероятный уровень с наблюдением |

PK `(forecast_id, obs_is_final)`: геомагнитные индексы публикуют сначала оперативно, а потом
уточняют задним числом, поэтому прогноз проверяют дважды — по оперативным и по окончательным
данным — и обе оценки хранятся отдельными строками (до двух на прогноз).

### `rolling_metrics` — скользящие метрики качества

Метрики качества источника прогноза за скользящее окно (`window_days` суток, например 90),
отдельно по величине, горизонту и источнику. PK `(computed_at, quantity, horizon_days, source,
window_days)`; `horizon_days` 1–3, `window_days` > 0, `n` ≥ 0 (CHECK). Все метрики допускают NULL.

| Поле | Смысл |
|---|---|
| `n` | число проверенных прогнозов в окне |
| `bss` | Brier skill score: выигрыш по оценке Брайера относительно климатологии (больше 0 — лучше неё) |
| `rps_mean` | средний ранжированный вероятностный балл |
| `reliability` | надёжность: насколько заявленные вероятности соответствуют наблюдаемым частотам (меньше — лучше) |
| `resolution` | разрешение: насколько прогноз различает ситуации (больше — лучше) |
| `sharpness` | резкость: насколько прогнозы отличаются от «одного и того же каждый день» |
| `tss_g1`, `tss_g2` | True Skill Statistic для события «уровень не ниже G1» / «не ниже G2» |

### Почему схема такая

| Решение | Зачем |
|---|---|
| PK `forecast_verification` — `(forecast_id, obs_is_final)`, а не `(forecast_id)` | иначе вторую оценку негде хранить |
| `forecast.feature_set_v` рядом с `features_t` | момент без версии набора не определяет строку признаков (наборы разных версий хранятся рядом); через строку признаков прогноз можно связать с периодом работы того или иного прибора |
| `rolling_metrics.resolution` и `sharpness` | две модели с одинаковой оценкой Брайера различаются разрешением и надёжностью: плохую калибровку можно исправить, отсутствие разрешения — нет |
| CHECK-ограничения | целостность: горизонт, окно, JSON-объект, `n`, `window_days` |

### Договорённости

- Значения `quantity`, `source` и ключи `distribution` / `observed_level` схемой не ограничены
  (TEXT/JSONB): их задаёт код прогнозов. Модель `ml` может появиться в двух вариантах (автономная,
  без прогноза SWPC среди признаков, и расширенная), а также ансамбль; раз `source` входит в
  UNIQUE, варианты должны называться по-разному.
- Словарь уровней пока не зафиксирован: в моках шесть уровней (`quiet`, `unsettled`, `active`,
  `G1`, `G2`, `G3+`); окончательный набор определит модель геомагнитной активности.

## Модель данных: суточные наблюдения (миграция 0003)

Пока создана только таблица `observation_daily` (модель `ObservationDailyRecord`, мок
`tests/mocks/observation.py`); таблицы признаков `feature_row` и отпечатка прибора ещё нет (см.
ниже).

Одна строка — одни сутки (`d`, первичный ключ). **NULL значит «нет данных»**: отсутствие не
подменяется нулём (если неизвестно, направлен ли выброс к Земле, это не то же самое, что «не
направлен»), поэтому всё, кроме даты, ключа сырья и времени загрузки, допускает NULL.

| Группа | Колонки |
|---|---|
| Геомагнитная обстановка | `kp_max`, `kp_values` (до 8 трёхчасовых значений индекса Kp), `kp_source` (`gfz`), `dst_min` (минимум индекса Dst, нТл), `dst_source` (`final`, `provisional` или `realtime`) |
| Солнечный ветер | `sw_speed_min`, `sw_speed_avg`, `sw_speed_max`, `sw_bt`, `sw_bz_min`, `sw_density_avg`\*, `sw_sector`\*, `sw_source` (`rtsw`, `omni_hro_1min` или `omni2_hourly`) |
| Вспышки | `flare_max_class`, `flare_counts` (JSONB, `{"B": 5, "C": 3, "M": 0, "X": 0}`) |
| Активные области | `ar_count`, `ar_total_area`, `ar_has_delta` (есть ли область с дельта-конфигурацией поля), `ar_summary` (JSONB: число областей по классам McIntosh и по росту `new`/`continuing`; структуру определит код загрузки наблюдений) |
| Корональные выбросы | `cme_count`, `cme_earth_directed`, `cme_direction_unknown` (направление не определено), `cme_speed_max`\*, `cme_width_max`\*, `cme_earth_directed_speed_max`\* |
| Служебные | `qc` (JSONB: статусы качества по группам полей), `raw_s3_key` (обязателен: сырой ответ API сохраняется раньше разбора), `ingested_at` (по умолчанию `now()`) |

\* — колонки добавлены для признаков `sw_density_avg`, `sw_sector`, `cme_speed_max_24h`,
`cme_width_max_24h` и `cme_earth_directed_speed_max_24h` из `contracts/features/specs.yaml`
([contracts.md](contracts.md)). Источники в ответе SWX API: `solar_wind.density.avg` (поле `latest`
бывает пустым), `solar_wind.imf.sector` (`toward` или `away`), `speed` и `width` выбросов.
`cme_earth_directed_speed_max` равна NULL, если выбросов к Земле не было.

Ограничения (CHECK):
- `kp_values` не длиннее 8. В ответе API Kp лежит в двух местах с разными значениями:
  `solar_wind.kp_obs` (24 значения) и `geomag.kp_obs` (8 значений, источник GFZ). Каноническим
  считается второй, и ограничение не даёт записать по ошибке первый;
- счётчики не отрицательны, а `cme_earth_directed + cme_direction_unknown <= cme_count`;
- `flare_counts`, `ar_summary` и `qc` — JSON-объекты.

## Облачный контур: YDB

В облаке оперативное состояние хранит YDB (serverless), а всё объёмное и неизменяемое — Object
Storage (сырьё, нормализованные сущности и признаки в Parquet, артефакты моделей, выпуски); SQL по
большим объёмам считает Yandex Query по Parquet. PostgreSQL остаётся локальной базой для разработки
и тестов, а таблицы и модели выше — эталоном логической схемы. Описание всего контура — в
[architecture.md](architecture.md).

### Что меняется при переходе с PostgreSQL

| В PostgreSQL | В YDB | Что делать |
|---|---|---|
| `BIGSERIAL`, последовательности | нет | идентификатор задаёт приложение: детерминированный `forecast_id` вида `2026-10-05/geomag_level/1/m0`, а не случайный — повторный прогон того же дня перезапишет запись, а не создаст дубль |
| `UNIQUE (issued_at, …)` | только первичный ключ | ключ начинается с даты: `(target_date, quantity, horizon_days, source)`; записи распределяются по диапазонам, горячего хвоста нет |
| `tstzrange` | нет | два столбца `Timestamp` (`target_from`, `target_to` — в моделях выше уже так) |
| `JSONB` с индексами и `CHECK` | `JsonDocument` | хранить и читать целиком; поиск по полям JSON не делать, нужное вынести в обычные столбцы |
| внешние ключи | нет | целостность на стороне приложения |
| частичные и выражения-индексы | нет | вторичный индекс по обычным столбцам, остальное — фильтром в запросе |
| оконные функции по большой истории | ограниченно | такие запросы — в Yandex Query по Parquet |

### Таблицы

Прогнозы хранятся **от всех источников**, а не только от победившего: иначе нельзя задним числом
сравнить, кто был прав, и селектору источника не на чем учиться (около 6600 строк в год).
`rolling_metrics` включает `computed_at` в ключ: метрики — не состояние, а история, и перезапись на
месте лишила бы возможности заметить деградацию.

| Локально (PostgreSQL) | В облаке |
|---|---|
| `forecast` | YDB `forecast`; копия в `lake/l2/forecasts` для аналитики |
| `forecast_verification` | YDB `forecast_verification` |
| `rolling_metrics` | YDB `rolling_metrics`; считается в Yandex Query по Parquet, в YDB кладётся результат (десяток строк) |
| `observation_daily` | не нужна: суточные наблюдения и признаки лежат в `lake` (`l1/…`, `l2/features`, `l2/targets`) |
| — | YDB `run`, `issue`, `model_registry`: прогоны воркфлоу, выпуски бюллетеня, реестр действующих версий |

Проект схемы YDB (в YDB не применялся: перед использованием выполните его на тестовой базе и
исправьте то, что она не примет):

```sql
-- Прогон воркфлоу
CREATE TABLE run (
    run_id        Utf8  NOT NULL,
    kind          Utf8  NOT NULL,          -- daily | verify | backfill
    target_date   Date  NOT NULL,
    started_at    Timestamp,
    finished_at   Timestamp,
    status        Utf8,                    -- running | ok | failed | degraded
    step_log      JsonDocument,            -- шаги, длительности, классы ошибок
    PRIMARY KEY (target_date, kind, run_id)
);

-- Выпуск бюллетеня
CREATE TABLE issue (
    target_date   Date  NOT NULL,
    issue_no      Uint8 NOT NULL,          -- несколько выпусков за сутки
    run_id        Utf8,
    verdict       Utf8,                    -- pass | repaired | fallback
    bulletin_key  Utf8,                    -- ссылка в Object Storage
    trace_key     Utf8,
    published_at  Timestamp,
    PRIMARY KEY (target_date, issue_no)
);

-- Прогнозы: все источники
CREATE TABLE forecast (
    target_date    Date  NOT NULL,
    quantity       Utf8  NOT NULL,
    horizon_days   Uint8 NOT NULL,
    source         Utf8  NOT NULL,
    forecast_id    Utf8  NOT NULL,
    issued_at      Timestamp,
    target_from    Timestamp,
    target_to      Timestamp,
    distribution   JsonDocument,
    point_estimate Utf8,
    model_version  Utf8,
    calibrator_version Utf8,
    support_n      Uint32,                 -- на скольких наблюдениях построена оценка
    fingerprint_key Utf8,                  -- отпечаток приборов
    PRIMARY KEY (target_date, quantity, horizon_days, source)
);

-- Верификация
CREATE TABLE forecast_verification (
    target_date    Date  NOT NULL,
    quantity       Utf8  NOT NULL,
    horizon_days   Uint8 NOT NULL,
    source         Utf8  NOT NULL,
    verified_at    Timestamp,
    observed_level Utf8,
    obs_source     Utf8,
    obs_is_final   Bool,
    brier          Double,
    rps            Double,
    log_score      Double,
    hit            Bool,
    PRIMARY KEY (target_date, quantity, horizon_days, source)
);

-- Скользящие метрики: читаются генерацией на каждом выпуске
CREATE TABLE rolling_metrics (
    quantity       Utf8   NOT NULL,
    horizon_days   Uint8  NOT NULL,
    source         Utf8   NOT NULL,
    window_days    Uint16 NOT NULL,
    computed_at    Timestamp NOT NULL,
    n              Uint32,
    bss            Double,
    reliability    Double,
    resolution     Double,
    sharpness      Double,
    rps_mean       Double,
    tss_g1         Double,
    PRIMARY KEY (quantity, horizon_days, source, window_days, computed_at)
);

-- Реестр действующих версий
CREATE TABLE model_registry (
    quantity       Utf8  NOT NULL,
    horizon_days   Uint8 NOT NULL,
    active_from    Timestamp NOT NULL,
    selected_source Utf8,
    model_version  Utf8,
    calibrator_version Utf8,
    reason         Utf8,                   -- почему селектор выбрал это
    PRIMARY KEY (quantity, horizon_days, active_from)
);
```

От локальных таблиц проект отличается, и это нужно решить до переноса:

- `forecast_verification` в YDB — одна строка на прогноз с признаком `obs_is_final`, а локально —
  до двух строк (по оперативным и по окончательным данным, PK `(forecast_id, obs_is_final)`).
  Если нужны обе оценки, `obs_is_final` придётся включить в ключ.
- В `rolling_metrics` проекта YDB нет `tss_g2`, а локально он есть; если TSS для порога G2 нужен,
  колонку надо добавить.
- В `forecast` YDB нет `features_t` и `feature_set_v` (какая строка признаков использована), а
  локально они есть; в проекте добавлены `support_n` и `fingerprint_key`.
- Источники прогноза (`source`) в облаке: `climatology`, `persistence`, `recurrence`, `swpc`, `m0`
  (перекалибровка внешнего прогноза), `m1`, `m2`; локально — `ml`, `persistence`, `recurrence`,
  `climatology`, `swpc`.
- Сумма вероятностей в `distribution` равна 1 с допуском 0,01; это проверяет приложение, схема —
  нет.

## Что ещё не сделано

- Таблица `feature_row` — строки признаков, на которых обучаются и работают модели: момент, версия
  набора признаков, JSON значений.
- Отпечаток прибора (`fingerprint_key`) в `feature_row` и `observation_daily`: идентификатор
  набора источников измерений (спутник, ряд данных), которым получены значения. Он нужен, чтобы
  замечать смену прибора и считать качество прогнозов отдельно для периодов с разными приборами.
- Представление `verbal_calibration` для проверки словесных формулировок («вероятна», «возможна»):
  как часто сбывалось сказанное. Оно создаётся после `0003`, должно брать одну оценку на прогноз
  (окончательную, иначе оперативную) и сравнивать уровни по порядковому номеру, а не как строки
  (`'quiet' >= 'G1'` в SQL истинно).
