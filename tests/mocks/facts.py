"""Моки фактов для тестов: фабрика ``make_fact`` и набор фактов по спокойному дню 2026-09-01.

Значения и формулировки взяты из реального снимка источника за 2026-09-01 (шесть областей,
восемь вспышек, три выброса, ветер до 449.6 км/с, Kp 2.333). Идентификаторы и шаблоны здесь
только для тестов: реестр фактов проекта лежит в ``contracts/facts/registry.yaml``.
"""

from datetime import UTC, datetime
from typing import Any

from heliogeo.facts import DisplayPolicy, Fact, FactKind, Provenance, QCStatus, Uncertainty

T_FROM = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
T_TO = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
FETCHED = datetime(2026, 9, 1, 12, 0, 5, tzinfo=UTC)

# Шаблоны утверждений и единицы для набора ниже.
_SPEED = DisplayPolicy(
    round_to=25,
    unit_text={"ru": "км/с", "en": "km/s"},
    statement={
        "ru": "Максимальная скорость солнечного ветра за период составила {value} {unit}.",
        "en": "Maximum solar wind speed over the period was {value} {unit}.",
    },
)
_BT = DisplayPolicy(
    round_to=1,
    unit_text={"ru": "нТл", "en": "nT"},
    statement={
        "ru": "Модуль межпланетного магнитного поля составил {value} {unit}.",
        "en": "The interplanetary magnetic field strength was {value} {unit}.",
    },
)
_KP = DisplayPolicy(
    formatter="kp",
    statement={
        "ru": "Максимальный Kp за период составил {value}.",
        "en": "Maximum Kp over the period was {value}.",
    },
)
_DST = DisplayPolicy(
    round_to=5,
    unit_text={"ru": "нТл", "en": "nT"},
    statement={
        "ru": "Минимальное значение Dst за период составило {value} {unit}.",
        "en": "Minimum Dst over the period was {value} {unit}.",
    },
)


def make_provenance(
    source: str = "swx_api:/data/2026-09-01#solar_wind.speed.max",
    method: str = "max over 1440 one-minute values",
    **overrides: Any,
) -> Provenance:
    """Происхождение по умолчанию: ветер из ответа источника за 2026-09-01."""
    params: dict[str, Any] = {
        "source": source,
        "method": method,
        "files": ("/opt/swx/data/omni_min/omni_min202609.asc",),
        "fetched": FETCHED,
        "fingerprint": "omni_hro_1min",
    }
    params.update(overrides)
    return Provenance(**params)


def make_fact(**overrides: Any) -> Fact:
    """Факт «максимальная скорость ветра за период» (449.6 км/с, статус ok).

    Любое поле можно переопределить именованным аргументом: ``make_fact(value=None,
    qc=QCStatus.MISSING)``, ``make_fact(kind=FactKind.COUNT, value=6, display=DisplayPolicy())``.
    """
    params: dict[str, Any] = {
        "id": "sw.speed.max_24h",
        "kind": FactKind.SCALAR,
        "value": 449.6,
        "unit": "km/s",
        "t_valid_from": T_FROM,
        "t_valid_to": T_TO,
        "provenance": make_provenance(),
        "qc": QCStatus.OK,
        "display": _SPEED,
    }
    params.update(overrides)
    return Fact(**params)


def make_missing_fact(**overrides: Any) -> Fact:
    """Факт «пик потока протонов >10 МэВ»: данных нет (статус missing, значение None)."""
    params: dict[str, Any] = {
        "id": "part.proton10.peak",
        "kind": FactKind.SCALAR,
        "value": None,
        "unit": "pfu",
        "qc": QCStatus.MISSING,
        "provenance": make_provenance(
            source="swx_api:/data/2026-09-01#particles.proton_gt10mev.peak",
            method="direct",
            files=(),
            fingerprint=None,
            note="5-минутного ряда нет, поле null",
        ),
        "display": DisplayPolicy(),
    }
    params.update(overrides)
    return make_fact(**params)


def make_quiet_day_facts() -> dict[str, Fact]:
    """Набор из одиннадцати фактов по спокойному дню 2026-09-01, по id."""
    facts = [
        make_fact(),
        make_fact(
            id="sw.imf.bt",
            value=5.17,
            unit="nT",
            display=_BT,
            provenance=make_provenance("swx_api:/data/2026-09-01#solar_wind.imf.bt", "direct"),
        ),
        make_fact(
            id="geo.kp.max_24h",
            value=2.333,
            unit=None,
            display=_KP,
            provenance=make_provenance(
                "swx_api:/data/2026-09-01#geomag.kp_obs.max",  # канонический источник Kp
                "max over eight 3-hour values",
                files=(),
                fingerprint="gfz",
            ),
        ),
        make_fact(
            id="geo.dst.min_24h",
            value=-15,
            unit="nT",
            display=_DST,
            provenance=make_provenance("swx_api:/data/2026-09-01#geomag.dst_min_day", "direct"),
        ),
        make_fact(
            id="flare.count_24h",
            kind=FactKind.COUNT,
            value=8,
            unit=None,
            display=DisplayPolicy(
                statement={
                    "ru": "За период зарегистрировано вспышек: {value}.",
                    "en": "{value} flares were recorded in the period.",
                }
            ),
            provenance=make_provenance("swx_api:/data/2026-09-01#flares", "count(flares)"),
        ),
        make_fact(
            id="flare.max_class_24h",
            kind=FactKind.CATEGORICAL,
            value="C5.7",
            unit=None,
            display=DisplayPolicy(
                statement={
                    "ru": "Самая сильная вспышка в рентгеновском диапазоне за период — класса {value}.",
                    "en": "The largest X-ray flare of the period was class {value}.",
                }
            ),
            provenance=make_provenance("swx_api:/data/2026-09-01#flares[].class", "max class"),
        ),
        make_fact(
            id="ar.count",
            kind=FactKind.COUNT,
            value=6,
            unit=None,
            display=DisplayPolicy(
                statement={
                    "ru": "На диске наблюдалось активных областей: {value}.",
                    "en": "{value} active regions were present on the disk.",
                }
            ),
            provenance=make_provenance("swx_api:/data/2026-09-01#active_regions", "count"),
        ),
        make_fact(
            id="cme.earth_directed_count",
            kind=FactKind.COUNT,
            value=0,
            unit=None,
            qc=QCStatus.DEGRADED,
            uncertainty=Uncertainty(
                confidence=0.7, note="1 из 3 выбросов с неопределённым направлением"
            ),
            display=DisplayPolicy(
                statement={
                    "ru": "За период выявлено выбросов в направлении Земли: {value}.",
                    "en": "{value} Earth-directed CMEs were identified in the period.",
                }
            ),
            provenance=make_provenance(
                "swx_api:/data/2026-09-01#cmes[].earth_directed", "count(earth_directed is true)"
            ),
        ),
        make_fact(
            id="forecast.storm.level",
            kind=FactKind.CATEGORICAL,
            value="G1",
            unit=None,
            uncertainty=Uncertainty(confidence=0.3),
            display=DisplayPolicy(
                value_map={
                    "ru": {"G1": "слабая", "G2": "умеренная"},
                    "en": {"G1": "minor", "G2": "moderate"},
                },
                statement={
                    "ru": "Ожидается {value} геомагнитная буря.",
                    "en": "A {value} geomagnetic storm is expected.",
                },
            ),
            provenance=make_provenance("swpc:3-day forecast", "direct", files=()),
        ),
        make_fact(
            id="sep.event",
            kind=FactKind.BOOLEAN,
            value=False,
            unit=None,
            display=DisplayPolicy(),
            provenance=make_provenance(
                "swx_api:/data/2026-09-01#particles.sep.gt10_event", "direct"
            ),
        ),
        make_missing_fact(),
    ]
    return {fact.id: fact for fact in facts}
