"""Контракты репозитория (каталог contracts/): приёмка задач B0-02 и B3-01."""

import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from heliogeo.contracts.features import FeatureGroup, FeaturesContract, load_feature_specs
from heliogeo.contracts.kinds import KINDS
from heliogeo.contracts.lock import LOCK_NAME, read_lock
from heliogeo.contracts.validate import validate_directory
from heliogeo.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"


@pytest.fixture(scope="module")
def specs() -> FeaturesContract:
    return load_feature_specs(CONTRACTS)


# --- B0-02: каталог и валидатор ---


def test_repository_contracts_pass_validation() -> None:
    report = validate_directory(CONTRACTS)
    problems = report.problems + [e for f in report.files for e in f.errors]
    assert report.ok, problems
    assert [f.path for f in report.files] == [kind.path for kind in KINDS]


def test_validator_command_succeeds_on_the_repository() -> None:
    """Критерий приёмки B0-02: `python -m heliogeo.contracts.validate` проходит."""
    env = {k: v for k, v in os.environ.items() if k != "CONTRACTS_DIR"}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "heliogeo.contracts.validate"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_contract_is_covered_by_the_lock_file() -> None:
    lock = read_lock(CONTRACTS)
    assert lock is not None, f"нет {LOCK_NAME}: выполните `make contracts-lock`"
    assert set(lock.files) == {kind.path for kind in KINDS}


def test_default_contracts_dir_is_the_repository_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)
    default = Settings(_env_file=None).contracts_dir
    assert default == CONTRACTS
    assert default.is_dir()


def test_validation_does_not_depend_on_line_endings(tmp_path: Path) -> None:
    """Рабочее дерево на Windows (autocrlf) — CRLF; хэши и lock-файл от этого не меняются."""
    copy = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, copy)
    for path in copy.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert validate_directory(copy).ok


# --- B3-01: спецификации признаков ---


def test_every_feature_declares_a_non_negative_delay(specs: FeaturesContract) -> None:
    """Критерий приёмки B3-01: у каждой записи есть задержка доступности."""
    assert specs.features
    for feature in specs.features:
        assert isinstance(feature.available_delay, timedelta), feature.id
        assert feature.available_delay >= timedelta(0), feature.id


def test_feature_ids_are_unique(specs: FeaturesContract) -> None:
    assert len(specs.by_id()) == len(specs.features)


def test_every_feature_group_is_represented(specs: FeaturesContract) -> None:
    assert {f.group for f in specs.features} == set(FeatureGroup)


def test_delays_given_in_the_specification_are_kept(specs: FeaturesContract) -> None:
    """Задержки, названные в ЭТАП1 §2.7: Kp — PT3H, каталог выбросов — P1D, ветер — PT1H."""
    by_id = specs.by_id()
    assert by_id["kp_max_1d"].available_delay == timedelta(hours=3)
    assert by_id["cme_earth_directed"].available_delay == timedelta(days=1)
    assert by_id["sw_speed_avg"].available_delay == timedelta(hours=1)
    assert by_id["swpc_g_scale"].available_delay == timedelta(0)


def test_external_forecast_group_is_exactly_the_swpc_scales(specs: FeaturesContract) -> None:
    """Автономная модель — это все группы, кроме external_forecast (ЭТАП1 §2.4)."""
    external = {f.id for f in specs.features if f.group is FeatureGroup.EXTERNAL_FORECAST}
    assert external == {"swpc_g_scale", "swpc_r_scale"}


def test_features_for_observation_daily_additions_are_declared(specs: FeaturesContract) -> None:
    """Признаки, для которых в observation_daily добавлены колонки (часть B0-05)."""
    assert {
        "sw_density_avg",
        "sw_sector",
        "cme_speed_max_24h",
        "cme_width_max_24h",
        "cme_earth_directed_speed_max_24h",
    } <= set(specs.by_id())


def test_delays_are_marked_preliminary_until_the_file_is_signed(specs: FeaturesContract) -> None:
    """Пока файл не подписан экспертом, каждая задержка помечена как предварительная."""
    if specs.is_signed:
        pytest.skip("файл подписан: пометки «предварительно» больше не обязательны")
    for feature in specs.features:
        if feature.id == "doy":  # дата известна заранее, подтверждать нечего
            continue
        assert feature.note is not None, feature.id
        assert feature.note.startswith("предварительно"), feature.id
