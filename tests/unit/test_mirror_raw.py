"""Запись сырья: байты как получены, манифест за дату, идемпотентность, замена содержимого."""

import json
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from heliogeo.mirror.raw import ManifestError, RawStore, SaveStatus
from heliogeo.storage.keys import key_manifest, key_raw_snapshot
from heliogeo.storage.objects import LocalObjectStore, sha256_hex

REQUESTED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
FETCHED = datetime(2026, 10, 7, 9, 30, 5, tzinfo=UTC)
# Тело с не-ASCII и нестандартными пробелами: пересериализация изменила бы байты.
BODY = '{"query_time":  "2026-09-01T12:00:00Z", "note": "спокойно"}\r\n'.encode()


@pytest.fixture
def objects(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "raw")


@pytest.fixture
def raw(objects: LocalObjectStore) -> RawStore:
    return RawStore(objects)


def _save(raw: RawStore, body: bytes = BODY, **overrides: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "endpoint": "data",
        "body": body,
        "requested_at": REQUESTED,
        "window": None,
        "no_live": True,
        "fetched_at": FETCHED,
    }
    args.update(overrides)
    return raw.save_snapshot(**args)  # type: ignore[arg-type]


def test_snapshot_key_encodes_moment_window_and_live_mode() -> None:
    moment = datetime(2026, 9, 1, 14, 5, tzinfo=timezone(timedelta(hours=2)))

    assert (
        key_raw_snapshot("data", moment, None, True)
        == "live/2026-09-01/1205/data_ut_day_nolive.json"
    )
    assert (
        key_raw_snapshot("data", REQUESTED, "15m", False)
        == "live/2026-09-01/1200/data_15m_live.json"
    )
    assert (
        key_raw_snapshot("text", REQUESTED, "15m", False) == "live/2026-09-01/1200/text_live.json"
    )


def test_snapshot_key_rejects_a_naive_moment_and_a_bad_window() -> None:
    with pytest.raises(ValueError):
        key_raw_snapshot("data", datetime(2026, 9, 1, 12, 0), None, True)
    with pytest.raises(ValueError):
        key_raw_snapshot("data", REQUESTED, "../x", True)


def test_body_is_stored_byte_for_byte(raw: RawStore, objects: LocalObjectStore) -> None:
    record = _save(raw)

    assert record.status is SaveStatus.STORED
    assert objects.get(record.entry.key) == BODY
    assert record.entry.sha256 == sha256_hex(BODY)
    assert record.entry.size_bytes == len(BODY)


def test_manifest_lists_what_was_stored(raw: RawStore, objects: LocalObjectStore) -> None:
    _save(raw)
    _save(raw, b'{"kind": "discussion"}', endpoint="text")

    body = json.loads(objects.get(key_manifest(date(2026, 9, 1))))
    assert body["date"] == "2026-09-01"
    assert [e["key"] for e in body["entries"]] == [
        "live/2026-09-01/1200/data_ut_day_nolive.json",
        "live/2026-09-01/1200/text_nolive.json",
    ]
    first = body["entries"][0]
    assert first["no_live"] is True
    assert first["window"] is None
    assert first["fetched_at"] == "2026-10-07T09:30:05Z"
    assert first["requested_at"] == "2026-09-01T12:00:00Z"


def test_manifest_tells_apart_live_and_no_live_requests(raw: RawStore) -> None:
    _save(raw, no_live=True)
    _save(raw, b"{}", no_live=False)

    modes = {e.key: e.no_live for e in raw.load_manifest(date(2026, 9, 1)).entries}
    assert modes == {
        "live/2026-09-01/1200/data_ut_day_nolive.json": True,
        "live/2026-09-01/1200/data_ut_day_live.json": False,
    }


def test_text_ignores_the_window(raw: RawStore) -> None:
    record = _save(raw, endpoint="text", window="15m")

    assert record.entry.window is None
    assert record.entry.key.endswith("/text_nolive.json")


def test_repeating_the_same_bytes_changes_nothing(raw: RawStore, objects: LocalObjectStore) -> None:
    first = _save(raw)
    manifest_before = objects.get(key_manifest(date(2026, 9, 1)))

    again = _save(raw, fetched_at=FETCHED + timedelta(days=1))

    assert again.status is SaveStatus.UNCHANGED
    assert again.entry == first.entry
    assert objects.get(key_manifest(date(2026, 9, 1))) == manifest_before


def test_different_bytes_replace_the_object_and_leave_a_trace(
    raw: RawStore, objects: LocalObjectStore, caplog: pytest.LogCaptureFixture
) -> None:
    first = _save(raw)
    with caplog.at_level(logging.WARNING):
        second = _save(raw, b'{"query_time": "2026-09-01T12:00:00Z"}')

    assert second.status is SaveStatus.REPLACED
    assert second.entry.replaced_sha256 == (first.entry.sha256,)
    assert objects.get(second.entry.key) == b'{"query_time": "2026-09-01T12:00:00Z"}'
    assert "changed" in caplog.text
    assert len(raw.load_manifest(date(2026, 9, 1)).entries) == 1


def test_a_lost_object_is_written_again_without_touching_the_manifest(
    raw: RawStore, objects: LocalObjectStore
) -> None:
    record = _save(raw)
    manifest_before = objects.get(key_manifest(date(2026, 9, 1)))
    (objects._root / record.entry.key).unlink()

    healed = _save(raw)

    assert healed.status is SaveStatus.STORED
    assert objects.get(record.entry.key) == BODY
    assert objects.get(key_manifest(date(2026, 9, 1))) == manifest_before


def test_dates_are_taken_in_utc(raw: RawStore) -> None:
    late = datetime(
        2026, 9, 1, 23, 30, tzinfo=timezone(timedelta(hours=-3))
    )  # 02:30 UTC 2 сентября

    record = _save(raw, requested_at=late)

    assert record.entry.key == "live/2026-09-02/0230/data_ut_day_nolive.json"
    assert len(raw.load_manifest(date(2026, 9, 2)).entries) == 1
    assert raw.load_manifest(date(2026, 9, 1)).entries == ()


def test_empty_body_and_naive_time_are_refused(raw: RawStore, objects: LocalObjectStore) -> None:
    with pytest.raises(ValueError):
        _save(raw, b"")
    with pytest.raises(ValueError):
        _save(raw, fetched_at=datetime(2026, 10, 7, 9, 30))
    assert objects.list_keys() == []


def test_a_damaged_manifest_is_reported_not_overwritten(
    raw: RawStore, objects: LocalObjectStore
) -> None:
    objects.put(key_manifest(date(2026, 9, 1)), b"not json")

    with pytest.raises(ManifestError):
        _save(raw)
    assert objects.get(key_manifest(date(2026, 9, 1))) == b"not json"
    assert objects.list_keys("live/") == []


def test_a_manifest_of_another_date_is_refused(raw: RawStore, objects: LocalObjectStore) -> None:
    _save(raw)
    wrong = objects.get(key_manifest(date(2026, 9, 1)))
    objects.put(key_manifest(date(2026, 9, 3)), wrong)

    with pytest.raises(ManifestError):
        raw.load_manifest(date(2026, 9, 3))
