"""Фабрики временных контрактов для тестов: полное валидное дерево contracts/ в tmp_path."""

from pathlib import Path
from typing import Any

import yaml

from heliogeo.contracts.kinds import KINDS
from heliogeo.contracts.lock import update_lock

ENVELOPE: dict[str, Any] = {"version": "0.1.0", "approved_by": None, "approved_at": None}
SIGNATURE: dict[str, Any] = {"approved_by": "Иванов И.И.", "approved_at": "2026-09-15"}


def make_feature(**overrides: Any) -> dict[str, Any]:
    """Запись признака для features/specs.yaml."""
    data: dict[str, Any] = {
        "id": "kp_max_1d",
        "group": "activity_history",
        "description": "Максимум трёхчасового Kp за 24 часа",
        "available_delay": "PT3H",
        "status": "planned",
    }
    data.update(overrides)
    return data


def make_signals_body(**overrides: Any) -> dict[str, Any]:
    """Тело минимального rules/signals.yaml: паспорт признака из make_feature и итог «всё в норме»."""
    body: dict[str, Any] = {
        "features": {
            "kp_max_1d": {
                "type": "number",
                "meaning": "Максимум трёхчасового Kp за 24 часа",
                "valid_range": [0, 9],
            }
        },
        "signals": [
            {
                "id": "all_clear",
                "severity": "info",
                "kind": "summary",
                "when": {"no_signals_at_least": "notice", "critical": "all_present"},
                "explain": "Все наблюдаемые параметры в норме.",
            }
        ],
    }
    body.update(overrides)
    return body


def contract_data(kind_name: str, **overrides: Any) -> dict[str, Any]:
    """Минимальное валидное содержимое контракта вида ``kind_name``: конверт плюс тело."""
    body: dict[str, Any] = {}
    if kind_name == "features":
        body = {"features": [make_feature()]}
    elif kind_name == "signals":
        body = make_signals_body()
    return {**ENVELOPE, **body, **overrides}


def write_contract(root: Path, relative: str, data: dict[str, Any]) -> Path:
    """Записать контракт как YAML (каталоги создаются)."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def make_contracts_tree(root: Path, *, lock: bool = False, **envelope: Any) -> Path:
    """Записать в ``root`` все зарегистрированные контракты (минимальные, по умолчанию черновики).

    Именованные аргументы переопределяют поля конверта у каждого файла, например
    ``make_contracts_tree(root, **SIGNATURE)`` даёт подписанные контракты. С ``lock=True``
    рядом создаётся и versions.lock.json.
    """
    for kind in KINDS:
        write_contract(root, kind.path, contract_data(kind.name, **envelope))
    if lock:
        update_lock(root)
    return root
