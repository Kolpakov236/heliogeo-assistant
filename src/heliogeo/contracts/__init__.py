"""Контракты: доменные YAML-файлы (каталог contracts/) под версионированием и подписью эксперта.

Пакет читает и проверяет контракты; сами данные лежат вне src/, в contracts/ корня репозитория.
Проверка всего каталога: ``python -m heliogeo.contracts.validate``.
"""

from heliogeo.contracts.envelope import ContractModel
from heliogeo.contracts.loader import ContractError

__all__ = ["ContractError", "ContractModel"]
