"""Тесты grounding: проверка чисел против JSON."""

from datetime import UTC

import pytest

from heliogeo.llm.grounding import (
    GroundingIssue,
    GroundingReport,
    check_grounding,
    dump_report_json,
)

PAYLOAD = {
    "metadata": {
        "issued_utc": "2026-05-12T00:00:00Z",
        "valid_until_utc": "2026-05-13T12:00:00Z",
        "generator_version": "1.0",
    },
    "solar_activity": {
        "active_regions": [
            {"noaa_num": 3664, "location": "N15W30", "area": 450, "spots": 25},
        ],
        "flares_last_24h": [
            {"time": "2026-05-12T03:15:00Z", "class": "M2.5", "ar": 3664},
        ],
        "f107": 165.0,
        "ssn": 120,
    },
    "solar_wind": {
        "speed_avg": 650.0,
        "speed_min": 450.0,
        "speed_max": 820.0,
        "density": 15.0,
        "bt": 22.0,
        "bz_min": -18.0,
    },
    "geospace": {
        "kp_observed": 7.0,
        "dst_observed": -150.0,
        "current_level": "storm",
    },
    "forecast": {
        "flare_m_prob": 75.0,
        "flare_x_prob": 30.0,
        "geomagnetic_g": "G3",
    },
}


def test_grounding_ok() -> None:
    """Все числа в тексте есть в payload → ok."""
    text = "Kp observed: 7.0. Dst observed: -150 nT. Speed: 650 km/s."
    report = check_grounding(PAYLOAD, text)
    assert report.ok, report.summary()
    assert report.checked_numbers >= 3


def test_grounding_hallucinated_number() -> None:
    """Выдуманное число → issue."""
    text = "Kp observed: 9.5 (severe storm)."  # 9.5 нет в payload
    report = check_grounding(PAYLOAD, text)
    assert not report.ok
    assert any(i.value == "9.5" for i in report.issues)


def test_grounding_partial_hallucination() -> None:
    """Часть чисел корректна, часть — нет."""
    text = "Kp 7.0, Dst -150, speed 999 km/s."  # 999 нет
    report = check_grounding(PAYLOAD, text)
    assert not report.ok
    assert any(i.value == "999" for i in report.issues)
    assert not any(i.value == "7.0" for i in report.issues)


def test_grounding_iso_dates_ignored() -> None:
    """ISO-даты не считаются галлюцинациями."""
    text = "Issued: 2026-05-12T00:00:00Z, valid until 2026-05-13T12:00:00Z."
    report = check_grounding(PAYLOAD, text)
    # Все числа дат (2026, 05, 12, 00, 00, 00) — это части ISO-строк,
    # они заменяются пробелами, остаются только те, что есть в payload.
    assert report.ok, report.summary()


def test_grounding_negative_numbers() -> None:
    """Отрицательные числа: -150 есть, -300 нет."""
    text = "Dst: -150 nT."
    report = check_grounding(PAYLOAD, text)
    assert report.ok

    text_bad = "Dst: -300 nT."
    report_bad = check_grounding(PAYLOAD, text_bad)
    assert not report_bad.ok
    assert any(i.value == "-300" for i in report_bad.issues)


def test_grounding_tolerance() -> None:
    """Округление в пределах tolerance — не галлюцинация."""
    text = "Speed: 650.0 km/s."  # 650.0 == 650.0
    report = check_grounding(PAYLOAD, text)
    assert report.ok

    # 650.1 — в пределах 1% от 650 — OK
    text_close = "Speed: 650.1 km/s."
    report_close = check_grounding(PAYLOAD, text_close, rel_tol=1e-2)
    assert report_close.ok


def test_grounding_accepts_pydantic_model() -> None:
    """check_grounding работает с Pydantic-моделью."""
    from datetime import datetime

    from heliogeo.schema import Metadata, UnifiedPayload

    now = datetime(2026, 5, 12, tzinfo=UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    text = "No data available."
    report = check_grounding(payload, text)
    assert report.ok


def test_grounding_empty_text() -> None:
    """Пустой текст → 0 проверенных чисел, ok."""
    report = check_grounding(PAYLOAD, "")
    assert report.ok
    assert report.checked_numbers == 0


def test_grounding_report_summary() -> None:
    """summary() даёт читаемый текст."""
    report = check_grounding(PAYLOAD, "Kp 7.0")
    assert "OK" in report.summary()

    report_bad = check_grounding(PAYLOAD, "Kp 9.9")
    assert "FAIL" in report_bad.summary()


def test_grounding_dump_report_json() -> None:
    """dump_report_json сериализует отчёт."""
    report = check_grounding(PAYLOAD, "Kp 9.9")
    out = dump_report_json(report)
    assert '"ok": false' in out
    assert '"value": "9.9"' in out


def test_grounding_issue_dataclass() -> None:
    """GroundingIssue — dataclass с полями."""
    issue = GroundingIssue(value="9.9", context="Kp 9.9", reason="not found in payload")
    assert issue.value == "9.9"
    assert issue.context == "Kp 9.9"
    assert issue.reason == "not found in payload"


def test_grounding_report_ok_property() -> None:
    """GroundingReport.ok — True при пустых issues."""
    assert GroundingReport().ok
    assert not GroundingReport(issues=[GroundingIssue("1", "x", "y")]).ok


def test_grounding_collects_numbers_from_strings() -> None:
    """Числа внутри строк JSON тоже учитываются."""
    payload = {"notes": "Flare M1.5 at 03:15"}
    text = "Flare class: M1.5."
    # 1.5 встречается в payload как часть "M1.5" → OK
    report = check_grounding(payload, text)
    assert report.ok, report.summary()


@pytest.mark.parametrize(
    "text,expected_ok",
    [
        ("Kp 7.0", True),
        ("Kp 7.5", False),  # разница 0.5 / 7 = 7% > 5%
        ("Dst -150", True),
        ("Dst -200", False),  # разница 50 / 150 = 33% > 5%
        ("Speed 650", True),
        ("Speed 1500", False),  # разница 150 / 650 = 23% > 5%
        ("Flare M2.5", True),
        ("Flare X99.0", False),
    ],
)
def test_grounding_parametrized(text: str, expected_ok: bool) -> None:
    """Параметризованная проверка разных чисел."""
    report = check_grounding(PAYLOAD, text)
    assert report.ok == expected_ok, f"{text!r}: {report.summary()}"
