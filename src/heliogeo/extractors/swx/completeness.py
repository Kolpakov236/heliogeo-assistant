"""Полнота ответа ``/data``: пришло ли всё, что нужно для выпуска и признаков.

Проверка ничего не отвергает и не подставляет: она называет, чего не хватает. Решение — выпускать ли
текст, выпускать ли с оговоркой или отказаться — принимает следующий слой по правилам деградации;
здесь только факты о данных. Списки ниже — первая редакция: что считать критичным, должен подтвердить
предметный эксперт.

* **critical** — без этого нельзя описать ни активность Солнца, ни геомагнитную обстановку: наличие
  разделов областей, вспышек и выбросов (пустой список допустим — спокойные сутки, но отсутствие
  раздела означает «неизвестно»), средняя скорость ветра и максимум Kp.
* **important** — вошли в признаки или в текст, но выпуск без них возможен с оговоркой.
* **products** — файлы источника, из которых собирается ответ. Для каждого перечислены допустимые
  замены (ряд ветра — OMNI 1-мин или почасовой).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from heliogeo.extractors.swx.response import SwxDataResponse

# Разделы-списки: наличие важнее содержимого, пустой список — нормальные спокойные сутки.
_LIST_FIELDS = frozenset({"active_regions", "flares", "cmes"})

CRITICAL_FIELDS: tuple[str, ...] = (
    "active_regions",
    "flares",
    "cmes",
    "solar_wind.speed.avg",
    "geomag.kp_obs.max",
)
IMPORTANT_FIELDS: tuple[str, ...] = (
    "solar_wind.density.avg",
    "solar_wind.imf.bz_min",
    "solar_wind.imf.sector",
    "geomag.dst_obs",
    "geomag.dst_min_day",
    "particles.sep.gt10_event",
    "particles.proton_gt10mev.daily_fluence",
    "geomag.fcst.raw_issued",
)
# Каждая группа — продукты-синонимы: достаточно любого из них.
EXPECTED_PRODUCTS: tuple[tuple[str, ...], ...] = (
    ("srs",),
    ("events",),
    ("donki_cme",),
    ("dayind",),
    ("omni_hro_1min", "omni2"),
    ("gfz_kp",),
    ("three_day_forecast",),
    ("daypre",),
)


@dataclass(frozen=True, kw_only=True)
class Completeness:
    """Чего не хватает в ответе.

    Attributes:
        critical: недостающие критичные поля (пути вида ``geomag.kp_obs.max``).
        important: недостающие важные поля.
        products: недостающие продукты; синонимы записаны через ``|``.
    """

    critical: tuple[str, ...] = ()
    important: tuple[str, ...] = ()
    products: tuple[str, ...] = ()

    @property
    def has_critical_gaps(self) -> bool:
        return bool(self.critical)

    def notes(self) -> list[str]:
        """Строки для ``notes`` ответа функции; пусто, если не хватает нечего."""
        notes = []
        if self.critical:
            notes.append("нет критичных данных: " + ", ".join(self.critical))
        if self.important:
            notes.append("нет важных данных: " + ", ".join(self.important))
        if self.products:
            notes.append("нет продуктов источника: " + ", ".join(self.products))
        return notes


def _lookup(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _is_present(data: Mapping[str, Any], path: str) -> bool:
    value = _lookup(data, path)
    if path in _LIST_FIELDS:
        return isinstance(value, list)
    return value is not None


def check_completeness(response: SwxDataResponse) -> Completeness:
    """Чего не хватает в ответе ``/data`` (его ``data``, приведённом к моменту запроса)."""
    data = response.data
    present_products = {source.product for source in response.sources}
    return Completeness(
        critical=tuple(p for p in CRITICAL_FIELDS if not _is_present(data, p)),
        important=tuple(p for p in IMPORTANT_FIELDS if not _is_present(data, p)),
        products=tuple(
            "|".join(group) for group in EXPECTED_PRODUCTS if not present_products & set(group)
        ),
    )
