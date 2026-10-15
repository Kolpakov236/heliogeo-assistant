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
- Типизированные ответы источника (`heliogeo.extractors.swx.response`): `SwxDataResponse` и
  `SwxTextResponse` с телом как получено (`raw`) и приведённым к моменту запроса (`data`), временем
  ответа, окном, `notes`, `sources` (`SourceRef`), списком выпусков `available` (`TextRef`),
  `availability`, `unknown_fields` и `problems`. Тело, не похожее на ответ эндпоинта, отвергается
  (`SwxResponseError`); остальное разбирается мягко; неизвестные поля ответа журналируются.
- Моки источника `tests/mocks/swx.py` (подставной сервер, готовые ответы клиента) и эталонные
  payload `fixtures/expected/` с тестом, который следит, чтобы изменения клиента не меняли payload.
- Классы ошибок шага `ErrorClass` (`heliogeo.pipeline.pipeline`): закрытый перечень из девяти
  классов (`SOURCE_UNAVAILABLE`, `SOURCE_MALFORMED`, `TRANSIENT`, `LLM_TIMEOUT`,
  `LLM_INVALID_OUTPUT`, `VALIDATION_FAILED`, `DATA_INSUFFICIENT`, `CONFIG_ERROR`, `INTERNAL`) и
  `classify_error`; по классу воркфлоу решает, повторять ли шаг. Упавший шаг (`PipelineStep`) и
  прогон (`PipelineResult`) несут `error_class`, у шага есть `retryable`. `SwxClientError` и
  `LLMError` получили вид `kind`, по которому определяется класс.
- Спецификация суточного воркфлоу Yandex Workflows (формат YaWL) в `deploy/workflows/daily.yaml`
  вместо прежней условной схемы: open_run → ingest → normalize → features → forecast → generate →
  validate → deliver, повтор по классу ошибки циклом «проверка → пауза → тот же шаг», ремонт
  ровно один раз, ветка `fallback` с шаблонным текстом, деградация при недоступном источнике.
  Расписание задаётся на самом воркфлоу и запускает его без входных данных, поэтому `run_id` и
  дату выдаёт первый шаг `open_run`.
  Тесты `tests/unit/test_workflow_specs.py` проверяют структуру, коды повторов, защиту от
  зацикливания и то, что в состояние запуска идут ключи, а не данные. На стенде спецификация не
  запускалась.
- Тест `tests/unit/test_time_sources.py`: системные часы читают только разрешённые места
  (`cli/commands/run.py`, `extractors/swx/client.py`, `contracts/envelope.py`).
- Документация: облачный контур в `docs/architecture.md` (хранилища, функции, общие правила,
  лимиты), раздел «Облачный контур: YDB» в `docs/database.md`, раздел «Что известно о данных
  источника» в `docs/data_sources.md` (охват архива, дыры, дефекты разбора трёхдневного прогноза).
- Ключи Object Storage (`heliogeo.storage.keys`): построители `key_raw_warehouse`, `key_raw_live`,
  `key_manifest`, `key_l1`, `key_l2`, `key_issue`, `key_artifact`, имена бакетов `bucket_name`; части ключа
  проверяются и не исправляются молча; разметка `dt=` нигде, кроме построителей, не допускается (тест).
- Интерфейс хранилища объектов `ObjectStore` и локальная реализация `LocalObjectStore` (`heliogeo.storage.objects`):
  запись целиком и атомарно, SHA-256 содержимого, листинг по префиксу, защита от выхода за каталог.
- Запись сырья (`heliogeo.mirror.raw`): `RawStore.save_snapshot` сохраняет тело ответа `/data` или `/text` байтами как
  получено по ключу `live/{дата}/{HHMM}/{endpoint}_{окно}_{live|nolive}.json` (`key_raw_snapshot`) и ведёт
  манифест `_manifest/{дата}.json` (хеш, размер, окно, `no_live`, время получения). Повтор тех же байтов ничего не
  меняет; другие байты заменяют объект с предупреждением и следом в манифесте.

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
- `SwxClient.get_snapshot` заменён на `get_data`, который возвращает `SwxDataResponse`; `get_text`
  возвращает `SwxTextResponse` и принимает `no_live`. Без `query_time` клиент берёт текущую минуту
  по своим часам (`clock`) и передаёт источнику конкретное время вместо `now`. Приведение к моменту
  запроса и предупреждение о расхождении моментов перенесены из экстрактора в ответ клиента.
- `SwxExtractor.extract` собирает payload из `SwxDataResponse.data` и возвращает текст как
  `SwxTextResponse`; ответ последнего вызова — `last_response` (`last_availability` сохранён).
- `filter_known_at` возвращает полную копию ответа: результат не делит вложенных объектов с
  исходным.
- `forecast.geomagnetic_g` считается по максимуму числовой сетки Kp прогноза
  (`geomag.fcst.kp_forecast`: G1 при Kp ≥ 5, G2 при ≥ 6, … G5 при 9), а не берётся из поля
  `g_scale` источника: разбор прогноза в источнике ненадёжен (сетка пуста во всём архиве, `s_scale`
  всегда `S1`, а в спокойные дни `g_scale` равен `G1` из фразы «No G1 … are expected»). Пока сетки
  в ответе нет, `forecast.geomagnetic_g` и `geospace.kp_fcst` равны `None`; эталонные payload в
  `fixtures/expected/` обновлены.
- Системные часы не читаются в извлекателе, продуктах и рендере: «сейчас» определяют только часы
  клиента источника (`SwxClient.resolve_moment` стал публичным, `SwxExtractor` им пользуется),
  момент выпуска продукта — это `metadata.issued_utc` payload (`GenerationResult.issued_utc`,
  `Product.metadata.issued_utc`). Продукт без этого момента не собирается (`ValueError`).
- `render_pdf` принимает `issued_utc` и печатает строку «Issued: …» вместо «Generated: <сейчас>»;
  документ воспроизводим: одинаковые аргументы дают одинаковые байты.
- Шапка промпта ICAO (`ISSUED`, `VALID UNTIL`) заполняется из `metadata` payload; раньше обе строки
  оставались пустыми.
- `contracts/features/specs.yaml` 0.1.1: примечания к `cycle_proxy` (F10.7 есть в ежедневных
  индексах), `swpc_g_scale` и `swpc_r_scale` (брать из числовой сетки Kp, а не из прозы).
- `heliogeo run` при ошибке шага печатает класс ошибки; `PipelineResult.summary` показывает его у
  упавших шагов.

### Removed
- Подстановка пустого payload, когда сборщик бросал `NotImplementedError` (`_empty_payload`): она
  молча выпускала бюллетень без данных и читала системные часы. Теперь это ошибка шага
  `build_payload`.

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
