# Unified JSON Schema

Контракт между extractor'ами и LLM. **Единый источник правды.**

- **Версия:** 1.0
- **Модель:** `heliogeo.schema.UnifiedPayload`
- **Формат:** JSON, UTF-8, времена в UTC (ISO 8601).

## Общая структура

```json
{
  "metadata": { ... },
  "solar_activity": { ... },
  "energetic_particles": { ... },
  "solar_wind": { ... },
  "geospace": { ... },
  "forecast": { ... },
  "images": { ... },
  "technical_issues": null
}
```

## Разделы

### 1. `metadata` — метаданные пейлоада

Модель: `heliogeo.schema.payload.Metadata`

| Поле | Тип | Обяз. | Описание |
|------|-----|-------|----------|
| `issued_utc` | `datetime` | ✅ | Время выпуска пейлоада (UTC) |
| `valid_until_utc` | `datetime` | ✅ | Время, до которого пейлоад действителен (UTC) |
| `generator_version` | `str` | ✅ | Версия генератора (по умолчанию `"1.0"`) |
| `source_products` | `list[str]` | ✅ | Источники |
**Пример:**

```json
{
  "issued_utc": "2026-05-12T00:00:00Z",
  "valid_until_utc": "2026-05-13T12:00:00Z",
  "generator_version": "1.0",
  "source_products": []
}
```

### 2. `solar_activity` — солнечная активность

Модель: `heliogeo.schema.solar_activity.SolarActivity`

| Поле | Тип | Описание |
|------|-----|----------|
| `active_regions` | `list[ActiveRegion]` | Список активных регионов |
| `flares_last_24h` | `list[Flare]` | Вспышки за последние 24 часа |
| `cme_list` | `list[CME]` | Список CME |
| `f107` | `float \| null` | Поток F10.7, sfu |
| `ssn` | `int \| null` | Sunspot Number |

#### `ActiveRegion` — активный регион

| Поле | Тип | Описание |
|------|-----|----------|
| `noaa_num` | `int` | Номер NOAA AR (например, `3664`) |
| `location` | `str` | Гелиографические координаты, напр. `N15W30` |
| `mcintosh` | `str \| null` | Класс McIntosh (Hsx, Ekc, Dai...) |
| `magnetic_class` | `str \| null` | `alpha`, `beta`, `beta-gamma`, `beta-gamma-delta` |
| `area` | `int \| null` | Площадь в миллионных долях полусферы |
| `spots` | `int \| null` | Количество пятен |
| `growth` | `str \| null` | `growing`, `stable`, `decaying` |

**Источник:** InfluxDB + CV-детектор (AR-детектор на magnetogram).

#### `Flare` — солнечная вспышка

| Поле | Тип | Описание |
|------|-----|----------|
| `time` | `datetime` | Время вспышки (UTC) |
| `class_` | `str` (alias: `class`) | Класс: A/B/C/M/X + число (напр. `M1.5`, `X10`) |
| `ar` | `int \| null` | Номер AR, если известен |
| `radio_signatures` | `list[str]` | `Type II`, `Type IV`, `Tenflare` |

**Источник:** InfluxDB (GOES XRS) + парсинг SWPC events.

**Валидация:** класс должен соответствовать `^[ABCMX]\d+(\.\d+)?$`.

#### `CME` — корональный выброс массы

| Поле | Тип | Описание |
|------|-----|----------|
| `time` | `datetime` | Время выброса (UTC) |
| `speed` | `float` | Скорость, км/с |
| `width` | `float` | Угловая ширина, градусы |
| `earth_directed` | `bool` | Направлен ли к Земле |

**Источник:** SOHO/LASCO C2/C3 catalog.

### 3. `energetic_particles` — энергичные частицы

Модель: `heliogeo.schema.particles.EnergeticParticles`

| Поле | Тип | Ед. | Описание |
|------|-----|-----|----------|
| `e_2mev` | `float \| null` | pfu | Поток электронов >2 MeV |
| `p_10mev_peak` | `float \| null` | pfu | Протоны >10 MeV, пик |
| `p_10mev_background` | `float \| null` | pfu | Протоны >10 MeV, фон |
| `p_100mev_peak` | `float \| null` | pfu | Протоны >100 MeV, пик |
| `sep_flag` | `bool` | — | Флаг SEP-события |

**Источник:** InfluxDB (GOES EPS).

**Порог SEP:** `p_10mev_peak ≥ 10 pfu` (по стандарту NOAA).

### 4. `solar_wind` — солнечный ветер

Модель: `heliogeo.schema.solar_wind.SolarWind`

| Поле | Тип | Ед. | Описание |
|------|-----|-----|----------|
| `speed_min` | `float \| null` | км/с | Минимальная скорость |
| `speed_avg` | `float \| null` | км/с | Средняя скорость |
| `speed_max` | `float \| null` | км/с | Максимальная скорость |
| `density` | `float \| null` | част/см³ | Плотность |
| `bt` | `float \| null` | нТл | Полная индукция ММП |
| `bz_min` | `float \| null` | нТл | Bz min |
| `bz_max` | `float \| null` | нТл | Bz max |
| `phi` | `float \| null` | градусы | Фи-угол ММП (0–360) |
| `temperature` | `float \| null` | K | Температура |
| `source` | `Literal["CH HSS", "CME", "unknown"]` | — | Источник |

**Источник:** InfluxDB (DSCOVR/ACE).

**Эвристика source:**
- `CH HSS` — высокая скорость + низкая плотность + устойчивый Bz.
- `CME` — резкий рост скорости + высокая плотность + сильный Bz.
- `unknown` — не удалось определить.

### 5. `geospace` — геокосмос

Модель: `heliogeo.schema.geospace.Geospace`

| Поле | Тип | Ед. | Описание |
|------|-----|-----|----------|
| `kp_observed` | `float \| null` | — | Kp наблюдаемый (0–9) |
| `dst_observed` | `float \| null` | нТл | Dst наблюдаемый |
| `current_level` | `Literal["quiet", "active", "storm"]` | — | Текущий уровень |
| `kp_fcst` | `float \| null` | — | Прогноз Kp |

**Источник:** InfluxDB (NOAA SWPC).

**Валидация:**
- Kp: `0 ≤ kp ≤ 9`.
- Dst: `-500 ≤ dst ≤ 50`.

**Пороги current_level:**
- `quiet`: Kp < 4 и Dst > -30.
- `active`: 4 ≤ Kp < 5 или -50 < Dst ≤ -30.
- `storm`: Kp ≥ 5 или Dst ≤ -50.

### 6. `forecast` — прогноз

Модель: `heliogeo.schema.forecast.Forecast`

| Поле | Тип | Ед. | Описание |
|------|-----|-----|----------|
| `flare_c_prob` | `float \| null` | % | Вероятность C-вспышек |
| `flare_m_prob` | `float \| null` | % | Вероятность M-вспышек |
| `flare_x_prob` | `float \| null` | % | Вероятность X-вспышек |
| `proton_prob` | `float \| null` | % | Вероятность протонного события |
| `geomagnetic_g` | `str \| null` | — | Прогноз G1–G5 (напр. `G3`) |
| `drivers` | `list[str]` | — | Драйверы: `Enlil`, `WSA`, ... |

**Источник:** Blobs (модельные прогнозы).

**Валидация:** вероятности `0 ≤ p ≤ 100`.

### 7. `images` — изображения

Модель: `heliogeo.schema.images.Images`

Все поля — `str | null` (URL на DAV-сервере):

| Поле | Описание |
|------|----------|
| `magnetogram` | Магнитограмма (HMI) |
| `ha` | H-alpha |
| `euv193` | EUV 193 Å (SDO/AIA) |
| `lasco_c2` | LASCO C2 coronagraph |
| `lasco_c3` | LASCO C3 coronagraph |
| `xray_plot` | График GOES XRS |

**Источник:** DAV-сервер.

### 8. `technical_issues` — технические проблемы

| Тип | Описание |
|-----|----------|
| `str \| null` | Свободный текст. Например: AFTN/AMHS gateway issues |

---

## Полный пример

См. `fixtures/payload_*.json` (когда появятся реальные данные).

## Валидация

Проверить любой JSON:

```bash
heliogeo validate path/to/payload.json
```

Валидаторы:
- `metadata.issued_utc` — ISO 8601 UTC.
- `solar_activity.flares_last_24h[].class_` — `^[ABCMX]\d+(\.\d+)?$`.
- `geospace.kp_observed` — `0 ≤ kp ≤ 9`.
- `geospace.dst_observed` — `-500 ≤ dst ≤ 50`.
- `forecast.flare_*_prob`, `proton_prob` — `0 ≤ p ≤ 100`.

## Changelog

| Версия | Дата | Изменения |
|--------|------|-----------|
| 1.0 | 2026-09-22 | Initial schema |

## Ссылки

- Pydantic-модели: `src/heliogeo/schema/`
- Валидаторы: `src/heliogeo/schema/validators.py`
- CLI: `heliogeo validate`
- Архитектура: `docs/architecture.md`
