"""Тесты конверта контракта: версия и подпись эксперта."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from heliogeo.contracts import envelope
from heliogeo.contracts.envelope import ContractModel, version_key


def _envelope(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"version": "0.1.0", "approved_by": None, "approved_at": None}
    data.update(overrides)
    return data


def _messages(exc_info: pytest.ExceptionInfo[ValidationError]) -> str:
    return " | ".join(error["msg"] for error in exc_info.value.errors())


@pytest.mark.parametrize("version", ["0.0.0", "0.1.0", "1.2.0", "2026.09.1", "2026.10.1"])
def test_valid_versions(version: str) -> None:
    """Подходят и семантические (0.1.0), и календарные (2026.10.1) версии."""
    assert ContractModel.model_validate(_envelope(version=version)).version == version


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "", "1.0.0.0", "1.0.x", " 1.0.0", "1.0.0\n"])
def test_malformed_versions_are_rejected(version: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(version=version))
    assert "X.Y.Z" in _messages(exc_info)


def test_non_ascii_digits_are_not_a_version() -> None:
    """Арабо-индийские цифры «٢٠٢٦» — это Unicode-цифры для \\d, но не версия контракта."""
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(version="٢٠٢٦.١٠.١"))
    assert "X.Y.Z" in _messages(exc_info)


@pytest.mark.parametrize("version", [1.0, 1, None, ["0.1.0"]])
def test_non_string_version_asks_for_quotes(version: Any) -> None:
    """Без кавычек YAML читает 1.0 как число — об этом подсказывает сообщение."""
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(version=version))
    assert "в кавычках" in _messages(exc_info)


def test_unsigned_draft_is_valid() -> None:
    contract = ContractModel.model_validate(_envelope())
    assert contract.is_signed is False
    assert (contract.approved_by, contract.approved_at) == (None, None)


@pytest.mark.parametrize("approved_at", ["2026-09-15", date(2026, 9, 15)], ids=["str", "date"])
def test_signed_contract_accepts_date_as_string_or_yaml_date(approved_at: Any) -> None:
    contract = ContractModel.model_validate(
        _envelope(approved_by="Иванов И.И.", approved_at=approved_at)
    )
    assert contract.is_signed is True
    assert contract.approved_at == date(2026, 9, 15)


def test_approver_name_is_stripped() -> None:
    contract = ContractModel.model_validate(
        _envelope(approved_by="  Иванов И.И.  ", approved_at="2026-09-15")
    )
    assert contract.approved_by == "Иванов И.И."


@pytest.mark.parametrize(
    "overrides",
    [{"approved_by": "Иванов И.И."}, {"approved_at": "2026-09-15"}],
    ids=["only-name", "only-date"],
)
def test_signature_fields_come_together(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(**overrides))
    assert "вместе" in _messages(exc_info)


@pytest.mark.parametrize(
    "name",
    ["<предметный эксперт>", "<имя>", "...", "TODO", "tbd", "N/A", "-", "", "   "],
)
def test_placeholder_instead_of_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(approved_by=name, approved_at="2026-09-15"))
    assert "заглушка" in _messages(exc_info)


def test_future_signature_date_is_rejected_with_one_day_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Допуск в сутки — на часовой пояс подписавшего."""
    monkeypatch.setattr(envelope, "_today", lambda: date(2026, 10, 2))
    signed = {"approved_by": "Иванов И.И."}

    ContractModel.model_validate(_envelope(**signed, approved_at="2026-10-03"))
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate(_envelope(**signed, approved_at="2026-10-04"))
    assert "в будущем" in _messages(exc_info)


def test_all_envelope_fields_are_required() -> None:
    """Ключи approved_by/approved_at обязательны, даже когда значение null."""
    with pytest.raises(ValidationError) as exc_info:
        ContractModel.model_validate({"version": "0.1.0"})
    missing = {error["loc"] for error in exc_info.value.errors() if error["type"] == "missing"}
    assert missing == {("approved_by",), ("approved_at",)}


def test_contract_is_immutable() -> None:
    contract = ContractModel.model_validate(_envelope())
    with pytest.raises(ValidationError):
        contract.version = "9.9.9"  # type: ignore[misc]


def test_version_key_compares_numerically() -> None:
    """Версии сравниваются по числам, а не по строкам."""
    assert version_key("2026.10.1") == (2026, 10, 1)
    assert version_key("0.10.0") > version_key("0.9.0")
    assert version_key("2026.10.1") > version_key("2026.9.3")
    assert sorted(["0.10.0", "0.2.0", "0.9.1"], key=version_key) == ["0.2.0", "0.9.1", "0.10.0"]
