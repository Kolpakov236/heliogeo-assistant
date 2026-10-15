"""Построители ключей Object Storage: раскладка из архитектуры и отказ на недопустимых частях."""

import re
from datetime import date
from pathlib import Path

import pytest

from heliogeo.storage.keys import (
    L1_ENTITIES,
    L2_KINDS,
    Bucket,
    bucket_name,
    key_artifact,
    key_issue,
    key_l1,
    key_l2,
    key_manifest,
    key_raw_live,
    key_raw_warehouse,
    validate_key,
)

DAY = date(2026, 10, 5)
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "heliogeo"


def test_raw_layout_repeats_the_source_layout() -> None:
    assert key_raw_warehouse("srs", 2026, "20260905SRS.txt") == "warehouse/srs/2026/20260905SRS.txt"
    assert key_raw_live(DAY, "0330", "data.json") == "live/2026-10-05/0330/data.json"
    assert key_manifest(DAY) == "_manifest/2026-10-05.json"


def test_lake_keys_use_hive_partitions_named_dt() -> None:
    assert key_l1("flares", DAY) == "l1/flares/dt=2026-10-05/part-000.parquet"
    assert key_l1("flares", DAY, part=12) == "l1/flares/dt=2026-10-05/part-012.parquet"
    assert key_l2("features", DAY) == "l2/features/dt=2026-10-05/part-000.parquet"


def test_every_l1_entity_and_l2_kind_has_a_key() -> None:
    assert len(L1_ENTITIES) == 7
    for entity in L1_ENTITIES:
        assert key_l1(entity, DAY).startswith(f"l1/{entity}/dt=")
    for kind in L2_KINDS:
        assert key_l2(kind, DAY).startswith(f"l2/{kind}/dt=")


def test_issue_key_allows_a_compound_run_id() -> None:
    run_id = "20261005T031422Z/daily/01J9ZQ8M0K3X5V7N2B4C6D8F0H"
    assert key_issue(DAY, run_id, "payload.json") == f"2026-10-05/{run_id}/payload.json"


def test_artifact_key() -> None:
    assert key_artifact("models", "m0", "2026.10.01", "model.json") == (
        "models/m0/2026.10.01/model.json"
    )
    assert key_artifact("registry", "current.json") == "registry/current.json"


@pytest.mark.parametrize(
    "build",
    [
        lambda: key_raw_warehouse("SRS", 2026, "a.txt"),
        lambda: key_raw_warehouse("srs/x", 2026, "a.txt"),
        lambda: key_raw_warehouse("srs", 26, "a.txt"),
        lambda: key_raw_warehouse("srs", 2026, "../a.txt"),
        lambda: key_raw_warehouse("srs", 2026, "a b.txt"),
        lambda: key_raw_live(DAY, "2460", "data.json"),
        lambda: key_raw_live(DAY, "330", "data.json"),
        lambda: key_l1("sunspots", DAY),
        lambda: key_l1("flares", DAY, part=1000),
        lambda: key_l1("flares", DAY, part=-1),
        lambda: key_l2("raw", DAY),
        lambda: key_issue(DAY, "2026-10-05T03:14:22Z/daily/ULID", "payload.json"),
        lambda: key_issue(DAY, "../daily/ULID", "payload.json"),
        lambda: key_issue(DAY, "a//b", "payload.json"),
        lambda: key_artifact("models"),
        lambda: key_artifact("models", ".."),
    ],
)
def test_invalid_parts_are_rejected_not_repaired(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        build()


def test_validate_key() -> None:
    assert validate_key("_manifest/2026-10-05.json") == "_manifest/2026-10-05.json"
    assert validate_key("l1/flares/dt=2026-10-05/part-000.parquet")
    for bad in ["", "/a", "a/", "a//b", "../a", "a/../b", "a/.hidden", "a b", "a\\b", "C:/a"]:
        with pytest.raises(ValueError):
            validate_key(bad)


def test_bucket_names() -> None:
    assert bucket_name("heliogeo-prod", Bucket.RAW) == "heliogeo-prod-raw"
    assert bucket_name("hg", Bucket.ARTIFACTS) == "hg-artifacts"
    for bad in ["", "-a", "a-", "Heliogeo", "heliogeo_prod", "x" * 60]:
        with pytest.raises(ValueError):
            bucket_name(bad, Bucket.LAKE)


def test_hive_partition_markup_is_written_only_by_the_key_builders() -> None:
    """Один напишет ``dt=``, другой ``date=``: Yandex Query увидит две схемы разделов."""
    pattern = re.compile(r"""["']dt=|\bf["'][^"']*dt=\{""")
    offenders = [
        str(path.relative_to(SOURCE_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if path.name != "keys.py" and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
