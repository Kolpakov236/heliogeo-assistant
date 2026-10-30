"""Прогноз Kp: интерфейс моделей и заглушки на их месте.

Моделей прогноза Kp в проекте пока нет, но отчёт уже должен знать, как они будут подключены. Поэтому
здесь описан контракт и поставлены **заглушки**: они честно отвечают «модель не подключена» и не
выдумывают значений. Когда появится настоящая модель, достаточно реализовать ``KpForecaster`` и
подставить её в ``run_forecasters``; промпт и проверку отчёта менять не придётся.

Прогноз — сетка Kp по трёхчасовым интервалам от ``start_utc``. Статус:

* ``ok`` — модель ответила, значения есть;
* ``stub`` — модель не подключена (заглушка), значений нет;
* ``unavailable`` — модель подключена, но ответить не смогла (исключение, нет данных).

Модуль не зависит от ``heliogeo.llm`` и ``heliogeo.generation`` (правило границ ``lint-imports``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from heliogeo.core.logging import get_logger
from heliogeo.features.compute import History

logger = get_logger(__name__)

KP_STEP = timedelta(hours=3)
KP_MIN, KP_MAX = 0.0, 9.0


class KpForecastStatus(StrEnum):
    """Состояние прогноза одной модели."""

    OK = "ok"
    STUB = "stub"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, kw_only=True)
class KpForecast:
    """Прогноз Kp одной модели.

    Attributes:
        model: имя модели.
        status: ``ok``, ``stub`` или ``unavailable``.
        issued_utc: момент, на который сделан прогноз (UTC, с часовым поясом).
        start_utc: начало первого трёхчасового интервала сетки; ``None`` без значений.
        values: Kp по трёхчасовым интервалам, 0–9; ``None`` без значений.
        note: пояснение (почему нет значений, чем модель отличается).
    """

    model: str
    status: KpForecastStatus
    issued_utc: datetime
    start_utc: datetime | None = None
    values: tuple[float, ...] | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.issued_utc.tzinfo is None:
            raise ValueError("issued_utc должно быть с часовым поясом")
        has_values = self.values is not None
        if (self.status is KpForecastStatus.OK) != has_values:
            raise ValueError("значения есть только у прогноза со статусом ok")
        if self.values is not None:
            if not self.values:
                raise ValueError("сетка прогноза не может быть пустой")
            if self.start_utc is None or self.start_utc.tzinfo is None:
                raise ValueError("у прогноза со значениями нужен start_utc с часовым поясом")
            if any(not KP_MIN <= value <= KP_MAX for value in self.values):
                raise ValueError("Kp должен быть от 0 до 9")

    @property
    def usable(self) -> bool:
        """Есть ли значения, на которые можно опираться."""
        return self.status is KpForecastStatus.OK

    @property
    def horizon_hours(self) -> int:
        """Сколько часов покрывает сетка (0 без значений)."""
        return len(self.values or ()) * int(KP_STEP.total_seconds() // 3600)

    @property
    def max_kp(self) -> float | None:
        """Максимум сетки или ``None`` без значений."""
        return max(self.values) if self.values else None

    def to_json(self) -> dict[str, Any]:
        """Представление для входа отчёта (только JSON-типы)."""
        return {
            "model": self.model,
            "status": self.status.value,
            "issued_utc": self.issued_utc.isoformat(),
            "start_utc": self.start_utc.isoformat() if self.start_utc else None,
            "values": list(self.values) if self.values is not None else None,
            "horizon_hours": self.horizon_hours,
            "max_kp": self.max_kp,
            "note": self.note,
        }


class KpForecaster(Protocol):
    """Модель прогноза Kp: по истории суточных наблюдений выдаёт сетку на ближайшие сутки."""

    @property
    def name(self) -> str:
        """Имя модели (под ним она попадает в отчёт)."""
        ...

    def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
        """Прогноз на момент ``issued_utc`` (только по данным не позже этого момента)."""
        ...


@dataclass(frozen=True)
class StubKpForecaster:
    """Заглушка на месте будущей модели: всегда «модель не подключена», значений нет."""

    name: str
    description: str

    def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
        return KpForecast(
            model=self.name,
            status=KpForecastStatus.STUB,
            issued_utc=issued_utc,
            note=f"Модель {self.name} ({self.description}) не подключена: прогноза нет.",
        )


def default_kp_forecasters() -> tuple[KpForecaster, ...]:
    """Места под будущие модели: заглушки с именами, под которыми модели появятся."""
    return (
        StubKpForecaster("kp_gbm", "градиентный бустинг по признакам, формат heliogeo.gbm.v1"),
        StubKpForecaster("kp_recurrence", "27-суточная повторяемость"),
        StubKpForecaster("kp_persistence", "персистентность: завтра как сегодня"),
    )


def run_forecasters(
    forecasters: Sequence[KpForecaster], history: History, issued_utc: datetime
) -> tuple[KpForecast, ...]:
    """Опросить модели по порядку; сбой одной не мешает остальным.

    Исключение модели превращается в прогноз ``unavailable`` с описанием: отчёт должен узнать, что
    модель не ответила, а не упасть.
    """
    forecasts: list[KpForecast] = []
    for forecaster in forecasters:
        try:
            forecasts.append(forecaster.forecast(history, issued_utc))
        except Exception as exc:  # noqa: BLE001 - любая ошибка модели не должна ронять отчёт
            logger.exception("Kp forecaster %s failed", forecaster.name)
            forecasts.append(
                KpForecast(
                    model=forecaster.name,
                    status=KpForecastStatus.UNAVAILABLE,
                    issued_utc=issued_utc,
                    note=f"Модель {forecaster.name} не ответила: {type(exc).__name__}: {exc}",
                )
            )
    return tuple(forecasts)
