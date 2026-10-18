"""Рантайм функций: разбор события, ответ воркфлоу, классы ошибок, предел размера ответа."""

import json
from typing import Any

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClientError
from heliogeo.functions.runtime import (
    MAX_RESPONSE_BYTES,
    FunctionError,
    FunctionEvent,
    FunctionOutput,
    run,
)
from heliogeo.pipeline.pipeline import ErrorClass

RUN_ID = "20261005T031422Z/daily/01J9ZQ8M0K3X5V7N2B4C6D8F0H"


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "run_id": RUN_ID,
        "date": "2026-09-01",
        "attempt": 2,
        "in": {"raw_data": "live/2026-09-01/2359/data_ut_day_nolive.json"},
        "cfg": "registry/current.json",
    }
    event.update(overrides)
    return event


def _ok(event: FunctionEvent) -> FunctionOutput:
    return FunctionOutput(
        out={"key": event.inputs["raw_data"]}, metrics={"rows": 3}, notes=("пропуск",)
    )


def test_a_valid_event_reaches_the_implementation_and_the_answer_has_the_contract_shape() -> None:
    seen: list[FunctionEvent] = []

    def impl(event: FunctionEvent) -> FunctionOutput:
        seen.append(event)
        return _ok(event)

    result = run(_event(), impl, step="demo")

    assert seen[0].run_id == RUN_ID
    assert seen[0].date.isoformat() == "2026-09-01"
    assert seen[0].inputs == {"raw_data": "live/2026-09-01/2359/data_ut_day_nolive.json"}
    assert result["ok"] is True
    assert result["out"] == {"key": "live/2026-09-01/2359/data_ut_day_nolive.json"}
    assert result["notes"] == ["пропуск"]
    assert result["metrics"]["rows"] == 3
    assert result["metrics"]["attempt"] == 2
    assert result["metrics"]["duration_ms"] >= 0
    assert "error" not in result


def test_the_answer_is_plain_json() -> None:
    result = run(_event(), _ok, step="demo")

    assert json.loads(json.dumps(result)) == result


def test_optional_event_fields_have_defaults() -> None:
    result = run(
        {"run_id": RUN_ID, "date": "2026-09-01"}, lambda e: FunctionOutput(out={}), step="d"
    )

    assert result["ok"] is True
    assert result["metrics"]["attempt"] == 1


def test_a_known_error_becomes_an_answer_with_its_class_and_retry_flag() -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        raise FunctionError(ErrorClass.DATA_INSUFFICIENT, "нет данных", detail_key="diag/x.txt")

    result = run(_event(), impl, step="demo")

    assert result["ok"] is False
    assert result["error"] == {
        "class": "DATA_INSUFFICIENT",
        "retryable": False,
        "message": "нет данных",
        "detail_key": "diag/x.txt",
    }
    assert result["metrics"]["attempt"] == 2


@pytest.mark.parametrize(
    ("kind", "expected", "retryable"),
    [
        ("unavailable", "SOURCE_UNAVAILABLE", True),
        ("not_found", "DATA_INSUFFICIENT", False),
        ("rejected", "CONFIG_ERROR", False),
        ("malformed", "SOURCE_MALFORMED", False),
    ],
)
def test_source_client_errors_are_classified_for_the_workflow(
    kind: str, expected: str, retryable: bool
) -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        raise SwxClientError("boom", kind=kind)  # type: ignore[arg-type]

    error = run(_event(), impl, step="demo")["error"]

    assert (error["class"], error["retryable"]) == (expected, retryable)


def test_an_unexpected_exception_is_internal_and_not_retried() -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        raise RuntimeError("неожиданно")

    result = run(_event(), impl, step="demo")

    assert result["error"]["class"] == "INTERNAL"
    assert result["error"]["retryable"] is False
    assert "RuntimeError" in result["error"]["message"]


def test_a_cause_chain_is_followed() -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        try:
            raise SwxClientError("down", kind="unavailable")
        except SwxClientError as exc:
            raise RuntimeError("обёртка") from exc

    assert run(_event(), impl, step="demo")["error"]["class"] == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "bad",
    [
        {"date": "2026-09-01"},
        {"run_id": RUN_ID},
        {"run_id": "", "date": "2026-09-01"},
        {"run_id": RUN_ID, "date": "вчера"},
        {"run_id": RUN_ID, "date": "2026-09-01", "attempt": 0},
        {"run_id": RUN_ID, "date": "2026-09-01", "extra": 1},
        {"run_id": RUN_ID, "date": "2026-09-01", "in": ["not", "a", "dict"]},
    ],
)
def test_a_bad_event_is_a_configuration_error_and_the_implementation_is_not_called(
    bad: dict[str, Any],
) -> None:
    called = []

    result = run(bad, lambda e: called.append(e) or FunctionOutput(out={}), step="demo")  # type: ignore[func-returns-value]

    assert result["ok"] is False
    assert result["error"]["class"] == "CONFIG_ERROR"
    assert result["error"]["retryable"] is False
    assert called == []


def test_the_attempt_is_returned_even_when_the_event_is_bad() -> None:
    result = run({"attempt": 4}, _ok, step="demo")

    assert result["metrics"]["attempt"] == 4


def test_an_oversized_answer_is_replaced_by_an_error() -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        return FunctionOutput(out={"blob": "x" * (MAX_RESPONSE_BYTES + 1)})

    result = run(_event(), impl, step="demo")

    assert result["ok"] is False
    assert result["error"]["class"] == "INTERNAL"
    assert (
        "256" in result["error"]["message"] or str(MAX_RESPONSE_BYTES) in result["error"]["message"]
    )
    assert len(json.dumps(result).encode()) < MAX_RESPONSE_BYTES


def test_notes_are_capped_so_that_they_cannot_blow_the_answer_up() -> None:
    notes = tuple(f"замечание {i} " + "я" * 1000 for i in range(80))

    result = run(_event(), lambda e: FunctionOutput(out={}, notes=notes), step="demo")

    assert result["ok"] is True
    assert len(result["notes"]) == 51
    assert result["notes"][-1] == "и ещё 30 замечаний"
    assert all(len(note) <= 300 for note in result["notes"][:-1])


def test_a_long_error_message_is_cut() -> None:
    def impl(event: FunctionEvent) -> FunctionOutput:
        raise FunctionError(ErrorClass.CONFIG_ERROR, "ж" * 5000)

    assert len(run(_event(), impl, step="demo")["error"]["message"]) == 500


def test_the_event_model_reads_the_in_field() -> None:
    event = FunctionEvent.model_validate(_event())

    assert event.inputs["raw_data"].startswith("live/")
    assert event.attempt == 2


def test_a_transport_error_from_the_source_is_classified_through_the_client() -> None:
    """``httpx``-ошибка внутри клиента становится ``SwxClientError`` до рантайма; здесь — только вид."""
    cause = httpx.ConnectTimeout("t")

    def impl(event: FunctionEvent) -> FunctionOutput:
        raise SwxClientError("timeout", kind="unavailable") from cause

    assert run(_event(), impl, step="demo")["error"]["retryable"] is True
