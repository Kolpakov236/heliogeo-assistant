"""Рантайм функций: единый разбор события, ответ и классы ошибок.

Контракт один для всех функций воркфлоу, поэтому собственный разбор события писать нельзя.

Событие: ``{"run_id", "date", "attempt", "in", "cfg"}``. ``in`` — ключи объектов, которые передали
предыдущие шаги (данные между шагами не ходят: лимит шага воркфлоу 2 МБ).

Ответ: ``{"ok": true, "out": {...}, "metrics": {...}, "notes": [...]}`` или ``{"ok": false,
"error": {"class", "retryable", "message", "detail_key"}, "metrics": {...}}``. Бизнес-ошибка — это
ответ, а не исключение: класс ошибки из закрытого перечня ``ErrorClass`` позволяет воркфлоу решить,
повторять ли шаг. Номер попытки возвращается в ``metrics.attempt``: по нему воркфлоу ограничивает
повторы. Ответ не больше 256 КБ (лимит платформы 3,5 МБ, шаг воркфлоу вмещает 2 МБ): длинное уходит
в Object Storage, в ответе остаётся ключ.

Системные часы здесь не читаются: время данных приходит в событии. Длительность замеряется
монотонными часами и временем данных не является.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heliogeo.core.logging import get_logger
from heliogeo.pipeline.pipeline import RETRYABLE_ERROR_CLASSES, ErrorClass, classify_error

logger = get_logger(__name__)

MAX_RESPONSE_BYTES = 256 * 1024
_MAX_NOTES = 50
_MAX_NOTE_CHARS = 300
_MAX_MESSAGE_CHARS = 500


class FunctionEvent(BaseModel):
    """Событие, с которым воркфлоу вызывает функцию.

    Attributes:
        run_id: идентификатор запуска воркфлоу.
        date: дата, за которую работает запуск.
        attempt: номер попытки шага, с единицы.
        inputs: ключи объектов от предыдущих шагов (поле ``in`` события).
        cfg: ключ конфигурации в Object Storage; может отсутствовать.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(min_length=1)
    date: date
    attempt: int = Field(default=1, ge=1)
    inputs: dict[str, Any] = Field(default_factory=dict, alias="in")
    cfg: str | None = None


@dataclass(frozen=True, kw_only=True)
class FunctionOutput:
    """Успешный результат реализации функции.

    Attributes:
        out: ключи объектов и другие небольшие значения для следующих шагов.
        metrics: числа и флаги для панели наблюдаемости.
        notes: что стоит знать о прогоне: пропуски, замечания источника. Не ошибка.
    """

    out: Mapping[str, Any]
    metrics: Mapping[str, int | float | str | bool] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


class FunctionError(Exception):
    """Ожидаемая ошибка реализации с заранее известным классом."""

    def __init__(self, error_class: ErrorClass, message: str, *, detail_key: str | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.detail_key = detail_key


Implementation = Callable[[FunctionEvent], FunctionOutput]


def _error_response(
    error_class: ErrorClass,
    message: str,
    metrics: dict[str, Any],
    detail_key: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "class": error_class.value,
            "retryable": error_class in RETRYABLE_ERROR_CLASSES,
            "message": message[:_MAX_MESSAGE_CHARS],
            "detail_key": detail_key,
        },
        "metrics": metrics,
    }


def _success_response(output: FunctionOutput, metrics: dict[str, Any]) -> dict[str, Any]:
    notes = [note[:_MAX_NOTE_CHARS] for note in output.notes[:_MAX_NOTES]]
    if len(output.notes) > _MAX_NOTES:
        notes.append(f"и ещё {len(output.notes) - _MAX_NOTES} замечаний")
    return {
        "ok": True,
        "out": dict(output.out),
        "metrics": {**output.metrics, **metrics},
        "notes": notes,
    }


def run(event: Mapping[str, Any], impl: Implementation, *, step: str) -> dict[str, Any]:
    """Выполнить реализацию функции по контракту и вернуть ответ.

    Args:
        event: событие как пришло от воркфлоу.
        impl: реализация; получает проверенное событие, возвращает ``FunctionOutput`` или бросает
            ``FunctionError`` (известный класс) либо любое исключение (класс определит
            ``classify_error``).
        step: имя шага для журнала (``ingest``, ``normalize``).

    Returns:
        Ответ функции, пригодный для JSON. Исключения наружу не выходят.
    """
    started = time.monotonic()
    raw_attempt = event.get("attempt")
    attempt = raw_attempt if isinstance(raw_attempt, int) and raw_attempt >= 1 else 1
    metrics: dict[str, Any] = {"attempt": attempt}

    try:
        parsed = FunctionEvent.model_validate(event)
    except ValidationError as exc:
        logger.error("step=%s event=function.bad_event errors=%s", step, exc.error_count())
        metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
        return _error_response(ErrorClass.CONFIG_ERROR, f"событие не разобрано: {exc}", metrics)

    metrics["attempt"] = parsed.attempt
    log_prefix = f"step={step} run_id={parsed.run_id} date={parsed.date.isoformat()}"
    logger.info("%s event=function.started attempt=%d", log_prefix, parsed.attempt)
    try:
        output = impl(parsed)
        metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
        response = _success_response(output, metrics)
    except FunctionError as exc:
        metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
        response = _error_response(exc.error_class, str(exc), metrics, exc.detail_key)
    except Exception as exc:
        metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
        logger.exception("%s event=function.crashed", log_prefix)
        response = _error_response(classify_error(exc), f"{type(exc).__name__}: {exc}", metrics)

    size = len(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_RESPONSE_BYTES:
        response = _error_response(
            ErrorClass.INTERNAL,
            f"ответ функции {size} байт, предел {MAX_RESPONSE_BYTES}: длинное уносите в Object Storage",
            metrics,
        )
    logger.info(
        "%s event=function.finished ok=%s duration_ms=%d bytes_out=%d",
        log_prefix,
        response["ok"],
        metrics["duration_ms"],
        min(size, MAX_RESPONSE_BYTES),
    )
    return response
