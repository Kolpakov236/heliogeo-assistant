"""Конверт контракта: version, approved_by, approved_at и правила их проверки."""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ENVELOPE_KEYS = ("version", "approved_by", "approved_at")

_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")  # \d принимает и не-ASCII цифры
# Заглушки вместо имени эксперта: «<предметный эксперт>», «...», «TODO» и т.п.
_PLACEHOLDER_RE = re.compile(r"<.*>|\.{2,}|-+|tbd|todo|n/?a", re.IGNORECASE)
# Подпись «завтрашним» числом возможна из-за часового пояса того, кто подписывает
_FUTURE_SLACK = timedelta(days=1)


def _today() -> date:
    return datetime.now(UTC).date()


def version_key(version: str) -> tuple[int, int, int]:
    """Ключ для сравнения версий: «2026.10.1» -> (2026, 10, 1)."""
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


class ContractModel(BaseModel):
    """Общая часть любого контракта — конверт; тело добавляют наследники.

    Версия — строка X.Y.Z: подходят и семантическая (0.1.0), и календарная (2026.10.1).
    Подпись эксперта — пара approved_by/approved_at: обе заполнены или обе null (черновик).
    """

    model_config = ConfigDict(frozen=True)

    version: str
    approved_by: str | None
    approved_at: date | None

    @field_validator("version", mode="before")
    @classmethod
    def _check_version(cls, value: Any) -> str:
        # Без кавычек YAML читает 1.0 как число, а не как строку версии
        if not isinstance(value, str):
            raise ValueError(
                f'версия должна быть строкой в кавычках, например "0.1.0" (получено {value!r})'
            )
        if not _VERSION_RE.fullmatch(value):
            raise ValueError(
                f"версия должна иметь вид X.Y.Z, например 0.1.0 или 2026.10.1 (получено {value!r})"
            )
        return value

    @field_validator("approved_by")
    @classmethod
    def _check_approver(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not name or _PLACEHOLDER_RE.fullmatch(name):
            raise ValueError(
                f"вместо имени эксперта заглушка ({value!r}); для черновика укажите null"
            )
        return name

    @model_validator(mode="after")
    def _check_approval(self) -> Self:
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError(
                "approved_by и approved_at заполняются вместе (для черновика оба null)"
            )
        if self.approved_at is not None and self.approved_at > _today() + _FUTURE_SLACK:
            raise ValueError(f"approved_at в будущем: {self.approved_at}")
        return self

    @property
    def is_signed(self) -> bool:
        """Подписан ли контракт экспертом."""
        return self.approved_by is not None
