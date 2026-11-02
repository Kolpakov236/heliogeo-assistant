"""Промпты отчёта: правила на месте, справочник совпадает с контрактом, вход читается однозначно."""

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from heliogeo.contracts.signals import System, load_signals
from heliogeo.features.compute import History
from heliogeo.forecast.kp import KpForecast, KpForecastStatus
from heliogeo.functions.stored import day_moment
from heliogeo.llm.grounding import check_grounding
from heliogeo.mirror.raw import RawStore
from heliogeo.report import prompt as report_prompt
from heliogeo.report.input import SECTIONS, build_report_input
from heliogeo.report.prompt import ReportPrompt, ReportPromptError, build_report_prompt
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import FETCHED_AT, QUIET_DATA, STORM_DATA, answered_for, load_fixture

QUIET_DAY = date(2026, 9, 1)
STORM_DAY = date(2024, 5, 11)
RULE_IDS = (
    *(f"F{i}" for i in range(1, 7)),
    *(f"N{i}" for i in range(1, 9)),
    *(f"S{i}" for i in range(1, 8)),
    *(f"G{i}" for i in range(1, 4)),
    *(f"P{i}" for i in range(1, 5)),
    "H1",
    "H2",
)
# поля, на которые ссылаются правила системного сообщения (кроме forbids_claims: он бывает не у каждого сигнала)
RULE_FIELDS = (
    "time_text", "day_text", "forecast_day_text", "window_hours", "kp_forecast_available",
    "forbidden_claims", "kp_forecasts", "technical_issues", "summary", "notes", "sections",
    "observed", "signals", "source", "status", "max_kp", "values", "title", "explain", "kind",
    "severity", "unaffected",
)  # fmt: skip
# единицы паспортов (по-русски) -> как они записаны в справочнике полей
UNITS = {
    "км/с": "km/s",
    "нТл": "nT",
    "частиц/см³": "p/cm3",
    "миллионных долей полусферы": "millionths of the solar hemisphere",
    "градусы": "degrees",
    "суток": "days",
    "Kp/сутки": "Kp в сутки",
}


def _prompt(tmp_path: Path, fixture: str, day: date, **kwargs: Any) -> ReportPrompt:
    store = LocalObjectStore(tmp_path / "raw")
    body = answered_for(load_fixture(fixture), day_moment(day))
    RawStore(store).save_snapshot(
        endpoint="data",
        body=json.dumps(body).encode(),
        requested_at=day_moment(day),
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )
    return build_report_prompt(build_report_input(day, store, **kwargs))


@pytest.fixture
def quiet(tmp_path: Path) -> ReportPrompt:
    return _prompt(tmp_path, QUIET_DATA, QUIET_DAY)


@pytest.fixture
def storm(tmp_path: Path) -> ReportPrompt:
    return _prompt(tmp_path, STORM_DATA, STORM_DAY)


def _between(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def _glossary(prompt: ReportPrompt) -> str:
    return _between(prompt.user, "СПРАВОЧНИК ПОЛЕЙ ВХОДА", "ЗАДАНИЕ ПО РАЗДЕЛАМ")


def _task(prompt: ReportPrompt, section: str) -> str:
    """Задание одного раздела: от его названия до названия следующего (или до конца задания)."""
    task = _between(prompt.user, "ЗАДАНИЕ ПО РАЗДЕЛАМ", "ИТОГОВАЯ ПОМЕТКА")
    start = task.index(f"\n{section}\n")
    index = SECTIONS.index(section)
    if index + 1 == len(SECTIONS):
        return task[start:]
    return task[start : task.index(f"\n{SECTIONS[index + 1]}\n", start)]


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


# --- Системные правила ---


def test_system_prompt_has_every_rule_and_no_template_syntax(quiet: ReportPrompt) -> None:
    for rule in RULE_IDS:
        assert re.search(rf"^{rule}\. ", quiet.system, re.MULTILINE), f"нет правила {rule}"
    assert "{{" not in quiet.system and "{%" not in quiet.system
    assert "АНГЛИЙСКОМ" in quiet.system and "markdown" in quiet.system


def test_system_prompt_names_only_fields_the_input_really_has(
    quiet: ReportPrompt, storm: ReportPrompt
) -> None:
    present = _keys(quiet.payload) | _keys(storm.payload)

    for field in RULE_FIELDS:
        assert field in quiet.system, f"правила не упоминают {field}: список в тесте устарел"
    assert not set(RULE_FIELDS) - present


def test_system_prompt_signal_ids_exist_in_the_contract(quiet: ReportPrompt) -> None:
    contract_ids = {rule.id for rule in load_signals().signals}
    mentioned = set(re.findall(r"geomag_[a-z0-9_]+", quiet.system))

    assert mentioned == {"geomag_recurrence_27d", "geomag_recurrence_54d"}
    assert mentioned <= contract_ids


def test_system_prompt_has_no_example_values_the_model_could_copy(quiet: ReportPrompt) -> None:
    text = re.sub(r"^[A-Z]\d\. ", "", quiet.system, flags=re.MULTILINE)  # номера правил
    text = re.sub(r"geomag_[a-z0-9_]+", "", text)  # идентификаторы сигналов
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text)) - set("012345")  # шкалы G1–G5, R1–R5
    assert numbers <= {"24", "30", "450", "570", "2.333"}  # примеры запретов и ссылка на окно


# --- Справочник полей ---


def test_every_observed_key_is_explained(quiet: ReportPrompt, storm: ReportPrompt) -> None:
    for prompt in (quiet, storm):
        glossary = _glossary(prompt)
        observed = {k for s in prompt.payload["sections"] for k in _keys(s["observed"])}
        missing = sorted(k for k in observed if not re.search(rf"\b{k}\b", glossary))
        assert not missing, f"в справочнике нет полей: {missing}"


def test_glossary_units_agree_with_the_contract(quiet: ReportPrompt) -> None:
    glossary = _glossary(quiet)
    checked = 0
    for name, profile in load_signals().features.items():
        lines = [ln for ln in glossary.splitlines() if re.search(rf"\b{name}\b", ln)]
        if profile.unit is None or not lines:
            continue
        assert profile.unit in UNITS, f"единица {profile.unit!r} не учтена в тесте"
        assert UNITS[profile.unit] in lines[0], f"{name}: ждём {UNITS[profile.unit]!r}"
        checked += 1
    assert checked >= 12


def test_glossary_explains_every_system_in_the_summary(quiet: ReportPrompt) -> None:
    glossary = _glossary(quiet)
    assert all(f"{system.value} —" in glossary for system in System)


# --- Запрещённые утверждения ---


def test_every_claim_of_the_contract_has_a_digitless_text() -> None:
    claims = {c for rule in load_signals().signals for c in rule.forbids_claims}

    assert claims <= set(report_prompt.CLAIM_TEXT)
    assert not any(re.search(r"\d", text) for text in report_prompt.CLAIM_TEXT.values())


def test_claim_without_text_is_an_error_not_a_silent_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_prompt, "CLAIM_TEXT", {})

    with pytest.raises(ReportPromptError, match="no_earth_directed_cme"):
        _prompt(tmp_path, QUIET_DATA, QUIET_DAY)


def test_forbidden_claim_reaches_the_user_prompt_in_words(quiet: ReportPrompt) -> None:
    assert quiet.payload["forbidden_claims"] == ["no_earth_directed_cme"]
    assert report_prompt.CLAIM_TEXT["no_earth_directed_cme"] in quiet.user
    assert "особых запретов нет" not in quiet.user


# --- Задание по разделам ---


def test_every_obligatory_signal_is_listed_in_its_own_section_only(storm: ReportPrompt) -> None:
    for section in storm.payload["sections"]:
        head, _, tail = _task(storm, section["name"]).partition("Можно упомянуть")
        for signal in section["signals"]:
            required = signal["severity"] != "info" or signal["kind"] == "missing_data"
            assert (f"[{signal['id']}]" in head) is required
            assert f"[{signal['id']}]" in (head if required else tail)
    assert "[geomag_storm_g5]" not in _task(storm, "Solar Wind")


def test_notes_name_the_feature_they_belong_to(storm: ReportPrompt) -> None:
    notes = [n for s in storm.payload["sections"] for n in s["notes"]]

    assert notes and all(re.match(r"^[a-z][a-z0-9_]+: ", n) for n in notes)
    assert any(n.startswith("ar_has_delta: ") for n in notes)


def test_sections_without_signals_say_so_explicitly(quiet: ReportPrompt) -> None:
    for section in SECTIONS:
        assert "сигналов уровня notice и выше" in _task(quiet, section)
    assert "[all_clear]" in quiet.user and "итоговой пометки нет" not in quiet.user


def test_no_summary_forbids_calling_the_whole_situation_calm(storm: ReportPrompt) -> None:
    assert "итоговой пометки нет: обстановку в целом спокойной не называй" in storm.user


def test_kp_flag_is_spelled_the_way_the_rules_expect(tmp_path: Path, quiet: ReportPrompt) -> None:
    class Model:
        name = "kp_real"

        def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
            return KpForecast(
                model=self.name,
                status=KpForecastStatus.OK,
                issued_utc=issued_utc,
                start_utc=datetime(2026, 9, 2, tzinfo=UTC),
                values=(2.0,),
            )

    real = _prompt(tmp_path, QUIET_DATA, QUIET_DAY, forecasters=[Model()])

    assert "kp_forecast_available равен false." in quiet.user
    assert "kp_forecast_available равен true." in real.user


# --- Вход читается однозначно ---


def test_json_in_the_prompt_is_exactly_the_checked_payload(quiet: ReportPrompt) -> None:
    block = quiet.user[quiet.user.index("ВХОДНЫЕ ДАННЫЕ") :].split("\n", 1)[1]

    assert json.loads(block) == json.loads(json.dumps(quiet.payload))
    assert "{{" not in quiet.user and "{%" not in quiet.user


def test_prompts_are_reproducible(tmp_path: Path) -> None:
    first = _prompt(tmp_path / "a", QUIET_DATA, QUIET_DAY)
    second = _prompt(tmp_path / "b", QUIET_DATA, QUIET_DAY)

    assert (first.system, first.user) == (second.system, second.user)


def test_skeleton_has_four_sections_with_two_blocks_each_in_order(quiet: ReportPrompt) -> None:
    skeleton = _between(quiet.user, "ТОЧНЫЙ ШАБЛОН ОТВЕТА", "СПРАВОЧНИК ПОЛЕЙ ВХОДА")
    wanted = (*SECTIONS, ".24 hr Summary...", ".Forecast...")

    lines = [ln for ln in skeleton.splitlines() if ln in wanted]

    assert lines == [x for name in SECTIONS for x in (name, ".24 hr Summary...", ".Forecast...")]


# --- Формат ответа и проверка чисел совместимы ---


def _good_text(payload: dict[str, Any]) -> str:
    activity = payload["sections"][0]["observed"]
    geospace = payload["sections"][3]["observed"]
    flare = activity["flares_top"][0]
    return (
        f"Solar Activity\n\n.24 hr Summary...\nSolar activity was low on {payload['day_text']} "
        f"with {activity['flares_c_24h']} C-class flares, the largest {flare['class']} at "
        f"{flare['time_text']}. There were {activity['ar_count']} numbered regions.\n\n"
        f".Forecast...\nIf current conditions persist, activity is expected to stay low over "
        f"the next {payload['window_hours']} hours.\n\n\n"
        f"Geospace\n\n.24 hr Summary...\nThe maximum Kp was {geospace['kp_max_1d']}. "
        f"Dst reached {geospace['dst_min_1d']} nT.\n\n"
        f".Forecast...\nNo Kp model guidance was available for this issue. Valid for "
        f"{payload['forecast_day_text']}.\n"
    )


def test_a_well_formed_report_passes_the_number_check(quiet: ReportPrompt) -> None:
    report = check_grounding(quiet.payload, _good_text(quiet.payload))

    assert report.ok, report.summary()
    assert report.checked_numbers >= 8  # шапка «24 hr» и даты тоже проверены


def test_hyphenated_range_is_caught_as_a_negative_number(quiet: ReportPrompt) -> None:
    text = _good_text(quiet.payload) + "Solar wind speed ranged 450-570 km/s.\n"

    report = check_grounding(quiet.payload, text)

    assert not report.ok and "-570" in {issue.value for issue in report.issues}
