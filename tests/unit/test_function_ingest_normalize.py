"""fn-ingest и fn-normalize: сквозной путь «источник → raw → L1 → lake» на подставном сервере."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient
from heliogeo.extractors.swx.extractor import payload_from_response
from heliogeo.functions import ingest as ingest_module
from heliogeo.functions.ingest import make_ingest
from heliogeo.functions.normalize import make_normalize
from heliogeo.functions.runtime import run
from heliogeo.functions.stored import load_data_response
from heliogeo.mirror.raw import RawStore
from heliogeo.pipeline.pipeline import ErrorClass
from heliogeo.schema.l1 import L1_SCHEMAS
from heliogeo.storage.keys import key_l1
from heliogeo.storage.lake import read_partition
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_TEXT,
    FakeSwxServer,
    answered_for,
    fixture_bytes,
    fixture_response,
    json_response,
    load_fixture,
)

DAY = date(2026, 9, 1)
RUN_ID = "20261005T031422Z/daily/01J9ZQ8M0K3X5V7N2B4C6D8F0H"
DATA_KEY = "live/2026-09-01/2359/data_ut_day_nolive.json"
TEXT_KEY = "live/2026-09-01/2359/text_nolive.json"
MANIFEST_KEY = "_manifest/2026-09-01.json"


def _event(day: date = DAY, **inputs: Any) -> dict[str, Any]:
    return {"run_id": RUN_ID, "date": day.isoformat(), "attempt": 1, "in": inputs}


def _faithful_data() -> httpx.Response:
    """Ответ источника, который «ответил» на запрошенный момент (без замечаний о времени)."""
    return json_response(answered_for(load_fixture(QUIET_DATA), QUIET_END_OF_DAY))


def _client(raw: RawStore) -> SwxClient:
    return SwxClient(
        base_url="https://swx.test", clock=lambda: FETCHED_AT, raw_sink=raw.save_snapshot
    )


@pytest.fixture
def raw_store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "raw")


@pytest.fixture
def lake_store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "lake")


@pytest.fixture
def ingest(raw_store: LocalObjectStore):  # type: ignore[no-untyped-def]
    impl = make_ingest(raw_store, _client)
    return lambda event: run(event, impl, step="ingest")


@pytest.fixture
def normalize(raw_store: LocalObjectStore, lake_store: LocalObjectStore):  # type: ignore[no-untyped-def]
    impl = make_normalize(raw_store, lake_store)
    return lambda event: run(event, impl, step="normalize")


# --- ingest ---


def test_ingest_saves_data_and_text_and_returns_their_keys(
    monkeypatch: pytest.MonkeyPatch, ingest, raw_store: LocalObjectStore
) -> None:
    server = FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])

    result = ingest(_event())

    assert result["ok"] is True
    assert result["out"] == {
        "raw_data": DATA_KEY,
        "raw_text": TEXT_KEY,
        "raw_manifest": MANIFEST_KEY,
    }
    assert raw_store.list_keys() == sorted([DATA_KEY, TEXT_KEY, MANIFEST_KEY])
    assert result["metrics"]["from_raw"] is False
    assert result["metrics"]["payload_ok"] is True
    assert result["notes"] == []
    # полные сутки, без live-данных, время всегда явное
    assert "/data/2026-09-01T23:59" in str(server.requests[0].url)
    assert server.requests[0].url.params["no_live"] == "true"


def test_ingest_asks_the_source_only_once_per_day(monkeypatch: pytest.MonkeyPatch, ingest) -> None:
    server = FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    first = ingest(_event())
    requests_after_first = len(server.requests)

    second = ingest(_event())

    assert len(server.requests) == requests_after_first
    assert second["out"] == first["out"]
    assert second["metrics"]["from_raw"] is True
    assert second["metrics"]["payload_ok"] is True


def test_ingest_without_the_text_is_still_ok_and_says_so(
    monkeypatch: pytest.MonkeyPatch, ingest
) -> None:
    FakeSwxServer(monkeypatch, [_faithful_data(), httpx.Response(404, content=b"{}")])

    result = ingest(_event())

    assert result["ok"] is True
    assert "raw_text" not in result["out"]
    assert any("текст сводки не получен" in note for note in result["notes"])


def test_ingest_retries_the_text_on_the_next_call(monkeypatch: pytest.MonkeyPatch, ingest) -> None:
    server = FakeSwxServer(
        monkeypatch,
        [_faithful_data(), httpx.Response(404, content=b"{}"), fixture_response(QUIET_TEXT)],
    )
    ingest(_event())

    again = ingest(_event())

    assert again["out"]["raw_text"] == TEXT_KEY
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    ("reply", "error_class", "retryable"),
    [
        (httpx.Response(503, content=b"{}"), "SOURCE_UNAVAILABLE", True),
        (httpx.Response(404, content=b"{}"), "DATA_INSUFFICIENT", False),
        (httpx.Response(400, content=b"{}"), "CONFIG_ERROR", False),
        (httpx.Response(200, content=b"<html>"), "SOURCE_MALFORMED", False),
        (json_response({"error": "x"}), "SOURCE_MALFORMED", False),
    ],
)
def test_ingest_failures_carry_the_class_the_workflow_needs(
    monkeypatch: pytest.MonkeyPatch,
    ingest,
    raw_store: LocalObjectStore,
    reply: httpx.Response,
    error_class: str,
    retryable: bool,
) -> None:
    FakeSwxServer(monkeypatch, [reply])

    result = ingest(_event())

    assert result["ok"] is False
    assert (result["error"]["class"], result["error"]["retryable"]) == (error_class, retryable)
    assert ErrorClass(result["error"]["class"])
    assert raw_store.list_keys() == []


def test_ingest_reports_missing_critical_data_but_keeps_what_came(
    monkeypatch: pytest.MonkeyPatch, ingest, raw_store: LocalObjectStore
) -> None:
    body = answered_for(load_fixture(QUIET_DATA), QUIET_END_OF_DAY)
    body["geomag"]["kp_obs"] = None
    FakeSwxServer(monkeypatch, [json_response(body), fixture_response(QUIET_TEXT)])

    result = ingest(_event())

    assert result["ok"] is True
    assert result["metrics"]["missing_critical"] == 1
    assert any("geomag.kp_obs.max" in note for note in result["notes"])
    assert DATA_KEY in raw_store.list_keys()


def test_ingest_warns_when_a_payload_for_generation_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch, ingest
) -> None:
    body = answered_for(load_fixture(QUIET_DATA), QUIET_END_OF_DAY)
    body["geomag"]["kp_obs"]["max"] = -1.0  # Kp ниже 0: payload не проходит схему (выше 9 — срезается)
    FakeSwxServer(monkeypatch, [json_response(body), fixture_response(QUIET_TEXT)])

    result = ingest(_event())

    assert result["ok"] is True
    assert result["metrics"]["payload_ok"] is False
    assert any("не собирается payload" in note for note in result["notes"])


def test_ingest_passes_source_remarks_on(monkeypatch: pytest.MonkeyPatch, ingest) -> None:
    # источник ответил на полдень, а запрошено 23:59: замечание клиента попадает в notes
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA), fixture_response(QUIET_TEXT)])

    result = ingest(_event())

    assert any(note.startswith("источник: ") for note in result["notes"])


# --- normalize ---


def test_normalize_writes_the_seven_l1_partitions_from_what_ingest_brought(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize, lake_store: LocalObjectStore
) -> None:
    FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    brought = ingest(_event())["out"]

    result = normalize(_event(**brought))

    assert result["ok"] is True
    assert result["out"]["l1_keys"] == {entity: key_l1(entity, DAY) for entity in L1_SCHEMAS}
    assert lake_store.list_keys("l1/") == sorted(key_l1(e, DAY) for e in L1_SCHEMAS)
    assert (result["metrics"]["rows_flares"], result["metrics"]["rows_active_regions"]) == (8, 6)
    assert result["metrics"]["problems"] == 0
    flares = read_partition(lake_store, "flares", DAY)
    assert all(row["raw_key"] == DATA_KEY for row in flares)


def test_normalize_finds_the_day_without_the_ingest_keys(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize
) -> None:
    """Ingest мог не отработать, но сутки скачаны прошлым запуском: ключ детерминирован."""
    FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    ingest(_event())

    result = normalize(_event())

    assert result["ok"] is True


def test_normalize_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize, lake_store: LocalObjectStore
) -> None:
    FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    ingest(_event())
    normalize(_event())
    before = {key: lake_store.head(key) for key in lake_store.list_keys()}

    normalize(_event())

    assert {key: lake_store.head(key) for key in lake_store.list_keys()} == before


def test_normalize_without_any_raw_data_is_a_degradation_not_a_crash(
    normalize, lake_store: LocalObjectStore
) -> None:
    result = normalize(_event())

    assert result["ok"] is False
    assert result["error"]["class"] == "DATA_INSUFFICIENT"
    assert result["error"]["retryable"] is False
    assert lake_store.list_keys() == []


def test_normalize_refuses_a_response_taken_before_the_end_of_the_day(
    raw_store: LocalObjectStore, normalize, lake_store: LocalObjectStore
) -> None:
    noon = RawStore(raw_store).save_snapshot(
        endpoint="data",
        body=fixture_bytes(QUIET_DATA),
        requested_at=QUIET_END_OF_DAY.replace(hour=12, minute=0),
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )

    result = normalize(_event(raw_data=noon.entry.key))

    assert result["ok"] is False
    assert result["error"]["class"] == "CONFIG_ERROR"
    assert "раньше конца суток" in result["error"]["message"]
    assert lake_store.list_keys() == []


def test_normalize_does_not_take_another_days_data(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize
) -> None:
    FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    ingest(_event())

    result = normalize(_event(date(2026, 9, 2), raw_data=DATA_KEY))

    assert result["ok"] is False
    assert result["error"]["class"] == "DATA_INSUFFICIENT"


def test_normalize_reports_a_damaged_raw_body_as_malformed(
    raw_store: LocalObjectStore, normalize
) -> None:
    RawStore(raw_store).save_snapshot(
        endpoint="data",
        body=b"{not json",
        requested_at=QUIET_END_OF_DAY,
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )

    result = normalize(_event())

    assert result["error"]["class"] == "SOURCE_MALFORMED"


def test_normalize_passes_dropped_rows_on_as_notes(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize
) -> None:
    body = answered_for(load_fixture(QUIET_DATA), QUIET_END_OF_DAY)
    del body["flares"][0]["class"]
    FakeSwxServer(monkeypatch, [json_response(body), fixture_response(QUIET_TEXT)])
    ingest(_event())

    result = normalize(_event())

    assert result["ok"] is True
    assert result["metrics"]["rows_flares"] == 7
    assert any("flares[0]" in note and "отброшена" in note for note in result["notes"])


def test_the_answers_have_what_the_workflow_reads(
    monkeypatch: pytest.MonkeyPatch, ingest, normalize
) -> None:
    """Воркфлоу читает ``.ok``, ``.out``, ``.error.class``, ``.error.retryable``, ``.metrics.attempt``."""
    FakeSwxServer(monkeypatch, [_faithful_data(), fixture_response(QUIET_TEXT)])
    good = ingest(_event())
    bad = normalize(_event(date(2026, 9, 5)))

    assert good["ok"] is True and isinstance(good["out"], dict)
    assert good["metrics"]["attempt"] == 1
    assert bad["ok"] is False
    assert set(bad["error"]) == {"class", "retryable", "message", "detail_key"}
    assert bad["metrics"]["attempt"] == 1


def test_the_entry_points_answer_a_bad_event_without_touching_the_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from heliogeo.core.config import get_settings
    from heliogeo.functions import normalize as normalize_module

    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        for entry in (ingest_module.handler, normalize_module.handler):
            result = entry({"nonsense": True})
            assert result["error"]["class"] == "CONFIG_ERROR"
    finally:
        get_settings.cache_clear()


def test_the_payload_for_generation_built_from_the_stored_raw_equals_the_reference(
    monkeypatch: pytest.MonkeyPatch, ingest, raw_store: LocalObjectStore
) -> None:
    """Из сохранённого сырья получается ровно тот payload, на котором сняты эталоны генерации."""
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA), fixture_response(QUIET_TEXT)])
    ingest(_event())

    response = load_data_response(raw_store, DAY, DATA_KEY)
    payload = payload_from_response(response).model_dump(mode="json")

    reference = (
        Path(__file__).resolve().parents[2] / "fixtures" / "expected" / "payload_quiet_2359.json"
    )
    assert payload == json.loads(reference.read_text(encoding="utf-8"))
    assert response.from_mirror is True
