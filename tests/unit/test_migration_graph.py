"""Тесты графа миграций Alembic. Читают только файлы ревизий, БД не нужна."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def test_single_head() -> None:
    """Ровно одна «голова»: параллельные ветки миграций нужно слить до слияния в main."""
    heads = _script().get_heads()
    assert len(heads) == 1, f"несколько голов миграций: {heads}"


def test_single_root_is_baseline() -> None:
    """Единственный корень цепочки — базовая ревизия 0001."""
    assert _script().get_bases() == ["0001"]


def test_history_is_linear() -> None:
    """У каждой ревизии не более одного родителя (merge-ревизий нет)."""
    for rev in _script().walk_revisions():
        assert not isinstance(rev.down_revision, tuple), f"{rev.revision}: merge-ревизия"


def test_file_names_match_revision_ids() -> None:
    """Имя файла начинается с номера ревизии: 0002_forecast_tables.py ↔ revision = '0002'."""
    for rev in _script().walk_revisions():
        assert rev.path is not None
        assert Path(rev.path).name.startswith(f"{rev.revision}_"), rev.path
