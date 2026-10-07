"""Системные часы читают только точки входа: остальной код получает время из события.

Функция, читающая часы, невоспроизводима: при повторном прогоне за прошлую дату она подставит
сегодняшний момент и испортит выпуск или признаки. Поэтому время данных приходит параметром
(момент запроса, ``metadata.issued_utc`` payload), а «сейчас» определяется в считанных местах.
"""

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "heliogeo"

# Чтение «сейчас». Замер длительности (time.monotonic, time.perf_counter) сюда не относится: это
# не время данных.
_CLOCK_CALL = re.compile(
    r"\b(?:datetime\.(?:now|utcnow|today)|date\.today|time\.time|time\.time_ns)\s*\("
)

# Где читать часы разрешено и зачем (путь от src/heliogeo)
ALLOWED = {
    "cli/commands/run.py": "точка входа: сравнивает запрошенный момент с «сейчас» для предупреждения",
    "extractors/swx/client.py": (
        "часы клиента по умолчанию (параметр clock): единственное место, где определяется «сейчас»"
    ),
    "contracts/envelope.py": "проверка контрактов: подпись не может быть датирована будущим",
}


def _clock_reads(path: Path) -> list[int]:
    """Номера строк, где код читает системные часы (комментарии не считаются)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        number
        for number, line in enumerate(lines, start=1)
        if _CLOCK_CALL.search(line) and not line.lstrip().startswith("#")
    ]


def test_system_clock_is_read_only_where_allowed() -> None:
    offenders = {
        path.relative_to(SOURCE_ROOT).as_posix(): lines
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if (lines := _clock_reads(path)) and path.relative_to(SOURCE_ROOT).as_posix() not in ALLOWED
    }
    assert not offenders, (
        "Системные часы читаются вне точек входа (файл: строки): "
        f"{offenders}. Передайте время параметром из события прогона."
    )


def test_allowed_list_has_no_stale_entries() -> None:
    """Файл, которому часы уже не нужны, убирается из списка: разрешения не копятся."""
    stale = [name for name in ALLOWED if not _clock_reads(SOURCE_ROOT / name)]
    assert not stale, f"В ALLOWED есть файлы без чтения часов: {stale}"


def test_the_guard_detects_clock_reads(tmp_path: Path) -> None:
    """Охранный тест умеет падать: чтение часов находится, замер длительности — нет."""
    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(
            [
                "from datetime import UTC, datetime, date",
                "import time",
                "a = datetime.now(UTC)",  # строка 3
                "b = datetime.utcnow()",  # 4
                "c = date.today()",  # 5
                "d = time.time()",  # 6
                "e = time.monotonic()",
                "f = time.perf_counter()",
                "g = lifetime.time(1)",
                "# h = datetime.now(UTC)",
            ]
        ),
        encoding="utf-8",
    )
    assert _clock_reads(source) == [3, 4, 5, 6]
