"""Бэкфилл архива: данные и тексты за период в сырой слой, затем L1 в озеро (часть ``fn-admin``).

За каждые сутки скачивается (всё с ``no_live``: ответ не зависит от момента скачивания):

* ``/data`` и ``/text`` на 23:59 — это ``fn-ingest``: идемпотентный, с манифестом, с проверкой
  полноты. На 23:59 источник отвечает последним выпуском суток — RSGA в 22:00;
* ``/text`` на 00:30 и на 12:30 — два выпуска Forecast Discussion. Ingest их не забирает: на 23:59
  они уже не «последний выпуск», хотя перечислены в ``available``.

Итого у суток три текстовых выпуска (Discussion 00:30, Discussion 12:30, RSGA 22:00) и одно тело
данных. Повторный запуск за тот же период в сеть не ходит: всё, что цело в сыром слое, берётся
оттуда.

Бережность к источнику: паузы между запросами (``ThrottledSink``) и размыкатель — после
нескольких подряд ошибок «повтор может помочь» (источник лежит) бэкфилл останавливается, а не
съедает время на заведомо проваленные запросы. Отсутствие данных за дату (дыра в архиве) на
размыкатель не влияет: это свойство даты, а не сбой источника.

Системные часы здесь не читаются: период задаёт вызывающий, паузы — переданная ``sleep``.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Literal

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.client import RawSink, SwxClient, SwxClientError
from heliogeo.functions.ingest import make_ingest
from heliogeo.functions.normalize import make_normalize
from heliogeo.functions.runtime import run
from heliogeo.functions.stored import is_stored
from heliogeo.mirror.raw import RawRecord, RawStore
from heliogeo.storage.keys import key_raw_snapshot
from heliogeo.storage.objects import ObjectStore

logger = get_logger(__name__)

# Выпуски Forecast Discussion, которые /text на 23:59 не отдаёт
DISCUSSION_TIMES: tuple[time, ...] = (time(0, 30), time(12, 30))

DEFAULT_DELAY_S = 0.4
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


class ThrottledSink:
    """Приёмник сырья с паузой после каждого сохранённого ответа.

    Клиент вызывает приёмник после каждого успешного скачивания, поэтому пауза ложится ровно между
    запросами к источнику. Неудачные запросы не замедляются: у клиента свои повторы с нарастающей
    задержкой.
    """

    def __init__(self, sink: RawSink, delay_s: float, sleep: Callable[[float], None]) -> None:
        self._sink = sink
        self._delay_s = delay_s
        self._sleep = sleep

    def __call__(
        self,
        *,
        endpoint: Literal["data", "text"],
        body: bytes,
        requested_at: datetime,
        window: str | None,
        no_live: bool,
        fetched_at: datetime,
    ) -> RawRecord:
        record = self._sink(
            endpoint=endpoint,
            body=body,
            requested_at=requested_at,
            window=window,
            no_live=no_live,
            fetched_at=fetched_at,
        )
        if self._delay_s > 0:
            self._sleep(self._delay_s)
        return record


@dataclass(frozen=True, kw_only=True)
class DayResult:
    """Итог по одним суткам.

    Attributes:
        day: сутки.
        ok: получено ли главное (для бэкфилла — тело ``/data``; для нормализации — разделы L1).
        from_raw: данные уже лежали в сыром слое, за ними в сеть не ходили (только бэкфилл).
        notes: замечания источника, пропуски продуктов, недостающие выпуски текста.
        error: класс и сообщение ошибки, если ``ok`` ложно.
        retryable: может ли повтор помочь (недоступность источника).
    """

    day: date
    ok: bool
    from_raw: bool = False
    notes: tuple[str, ...] = ()
    error: str | None = None
    retryable: bool = False


@dataclass
class BackfillReport:
    """Итог бэкфилла или нормализации по периоду."""

    results: list[DayResult] = field(default_factory=list)
    stopped_early: bool = False

    @property
    def ok_days(self) -> list[date]:
        return [r.day for r in self.results if r.ok]

    @property
    def failed_days(self) -> list[date]:
        return [r.day for r in self.results if not r.ok]

    @property
    def notes_count(self) -> int:
        return sum(len(r.notes) for r in self.results)


def days_between(start: date, end: date) -> list[date]:
    """Сутки от ``start`` до ``end`` включительно.

    Raises:
        ValueError: если ``end`` раньше ``start``.
    """
    if end < start:
        raise ValueError(f"конец периода {end} раньше начала {start}")
    return [date.fromordinal(n) for n in range(start.toordinal(), end.toordinal() + 1)]


def _ensure_discussions(
    day: date,
    raw: RawStore,
    raw_store: ObjectStore,
    client_factory: Callable[[RawStore], SwxClient],
) -> list[str]:
    """Докачать выпуски Discussion за сутки; вернуть замечания (пропуск выпуска — не отказ)."""
    notes: list[str] = []
    client: SwxClient | None = None
    for slot in DISCUSSION_TIMES:
        moment = datetime.combine(day, slot, tzinfo=UTC)
        if is_stored(raw, raw_store, day, key_raw_snapshot("text", moment, None, True)):
            continue
        client = client or client_factory(raw)
        try:
            response = client.get_text(query_time=moment, no_live=True)
        except SwxClientError as exc:
            notes.append(f"выпуск {slot:%H:%M} не получен: {exc}")
            continue
        if response.issued_dt != moment:
            # Источник ответил более ранним выпуском: тело сохранено под ключом этого времени, но
            # выпуска на него нет. Корпус опознаёт такие тела по времени выпуска и не дублирует.
            notes.append(
                f"выпуска {slot:%H:%M} нет: источник ответил выпуском {response.issued_dt}"
            )
    return notes


def _event(day: date, run_id: str) -> dict[str, object]:
    return {"run_id": run_id, "date": day.isoformat(), "attempt": 1}


def _failure(day: date, response: dict[str, object]) -> DayResult:
    error = response["error"]
    assert isinstance(error, dict)
    return DayResult(
        day=day,
        ok=False,
        error=f"{error['class']}: {error['message']}",
        retryable=bool(error["retryable"]),
    )


def backfill(
    days: Iterable[date],
    raw_store: ObjectStore,
    *,
    client_factory: Callable[[RawStore], SwxClient],
    run_id: str,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    on_day: Callable[[DayResult], None] | None = None,
) -> BackfillReport:
    """Скачать данные и тексты за сутки ``days`` в сырой слой.

    Args:
        days: сутки по порядку (см. ``days_between``).
        raw_store: бакет ``raw``.
        client_factory: строит клиента, сохраняющего ответы в переданный ``RawStore``; паузы
            между запросами задаются оборачиванием приёмника в ``ThrottledSink``.
        run_id: идентификатор запуска для журнала.
        max_consecutive_failures: после стольких подряд ошибок, где повтор мог бы помочь,
            бэкфилл останавливается (``stopped_early``).
        on_day: вызывается после каждых суток (прогресс).
    """
    ingest = make_ingest(raw_store, client_factory)
    raw = RawStore(raw_store)
    report = BackfillReport()
    consecutive = 0

    for day in days:
        response = run(_event(day, run_id), ingest, step="ingest")
        if response["ok"]:
            consecutive = 0
            notes = [*response["notes"], *_ensure_discussions(day, raw, raw_store, client_factory)]
            result = DayResult(
                day=day,
                ok=True,
                from_raw=bool(response["metrics"].get("from_raw")),
                notes=tuple(notes),
            )
        else:
            result = _failure(day, response)
            consecutive = consecutive + 1 if result.retryable else 0

        report.results.append(result)
        if on_day is not None:
            on_day(result)
        if consecutive >= max_consecutive_failures:
            logger.error("Backfill stopped: %d retryable failures in a row", consecutive)
            report.stopped_early = True
            break
    return report


def normalize_days(
    days: Iterable[date],
    raw_store: ObjectStore,
    lake_store: ObjectStore,
    *,
    run_id: str,
    on_day: Callable[[DayResult], None] | None = None,
) -> BackfillReport:
    """Превратить сохранённые ответы ``/data`` в Parquet-разделы L1 (``fn-normalize``).

    Сутки без сохранённого тела дают ошибку ``DATA_INSUFFICIENT`` по этим суткам и не мешают
    остальным.
    """
    normalize = make_normalize(raw_store, lake_store)
    report = BackfillReport()
    for day in days:
        response = run(_event(day, run_id), normalize, step="normalize")
        if response["ok"]:
            result = DayResult(day=day, ok=True, notes=tuple(response["notes"]))
        else:
            result = _failure(day, response)
        report.results.append(result)
        if on_day is not None:
            on_day(result)
    return report
