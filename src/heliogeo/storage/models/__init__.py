"""ORM-модели таблиц PostgreSQL.

Импорт пакета регистрирует все модели в ``Base.metadata`` (её использует migrations/env.py).
"""

from heliogeo.storage.models.base import Base
from heliogeo.storage.models.forecast import (
    ForecastRecord,
    ForecastVerificationRecord,
    RollingMetricsRecord,
)

__all__ = [
    "Base",
    "ForecastRecord",
    "ForecastVerificationRecord",
    "RollingMetricsRecord",
]
