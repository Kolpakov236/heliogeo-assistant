"""Границы модулей: контракты import-linter из pyproject.toml и их чувствительность.

Контракты проверяются на самом репозитории и на временном пакете ``demo`` с намеренными
нарушениями. Так видно, что каждый контракт действительно роняет проверку, а не просто записан
в конфигурации.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# lint-imports запускается как модуль Python: не зависит от PATH и от способа запуска pytest.
# --no-cache: тесты не оставляют .import_linter_cache; --no-logo: без псевдографики в выводе.
_LINT_IMPORTS = [
    sys.executable,
    "-c",
    "import sys; from importlinter.cli import lint_imports_command; "
    "sys.exit(lint_imports_command())",
    "--no-logo",
    "--no-cache",
]

GENERATION = "generation не ходит в хранилище напрямую"
VALIDATION = "validation не видит промптов генератора"
FORECAST = "forecast не зависит от llm"

# Контракты, которые обязаны быть в pyproject.toml: имя -> (источник, запрещённые модули).
REQUIRED_CONTRACTS = {
    GENERATION: (["heliogeo.generation"], ["heliogeo.storage", "heliogeo.extractors"]),
    VALIDATION: (["heliogeo.validation"], ["heliogeo.llm.prompts"]),
    FORECAST: (["heliogeo.forecast"], ["heliogeo.llm", "heliogeo.generation"]),
}

# Чистое дерево по образцу реального: разрешённые импорты не должны давать ложных срабатываний.
# В том числе llm/__init__ подтягивает prompts, как в heliogeo.
CLEAN_TREE = {
    "demo/__init__.py": "",
    "demo/core/__init__.py": "",
    "demo/storage/__init__.py": "",
    "demo/extractors/__init__.py": "from demo import core\n",
    "demo/llm/__init__.py": "from demo.llm import base, prompts\n",
    "demo/llm/base.py": "",
    "demo/llm/prompts.py": "",
    "demo/generation/__init__.py": "",
    "demo/generation/writer.py": "from demo import core, llm\n",
    "demo/validation/__init__.py": "",
    "demo/validation/checks.py": "from demo import core\nfrom demo.llm import base\n",
    "demo/forecast/__init__.py": "",
    "demo/forecast/model.py": "from demo import core, storage\n",
}


def _repo_contracts() -> list[dict[str, Any]]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts: list[dict[str, Any]] = data["tool"]["importlinter"]["contracts"]
    return contracts


def _demo_config(contracts: list[dict[str, Any]]) -> str:
    """Конфиг import-linter для пакета demo с теми же контрактами, что в pyproject.toml."""

    def rename(modules: list[str]) -> list[str]:
        return [re.sub(r"^heliogeo", "demo", module) for module in modules]

    lines = ["[tool.importlinter]", 'root_packages = ["demo"]', ""]
    for contract in contracts:
        lines += [
            "[[tool.importlinter.contracts]]",
            f"name = {json.dumps(contract['name'], ensure_ascii=False)}",
            f"type = {json.dumps(contract['type'])}",
            f"source_modules = {json.dumps(rename(contract['source_modules']))}",
            f"forbidden_modules = {json.dumps(rename(contract['forbidden_modules']))}",
            "",
        ]
    return "\n".join(lines)


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _run_lint_imports(
    cwd: Path, *, config: Path | None = None, pythonpath: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if pythonpath is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(pythonpath), env.get("PYTHONPATH")]))
    args = [*_LINT_IMPORTS]
    if config is not None:
        args += ["--config", str(config)]
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _broken_contracts(output: str) -> set[str]:
    return {
        line.removesuffix(" BROKEN") for line in output.splitlines() if line.endswith(" BROKEN")
    }


def _check_demo_tree(
    tmp_path: Path, extra_files: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    _write_tree(tmp_path, {**CLEAN_TREE, **extra_files})
    config = tmp_path / "pyproject.toml"
    config.write_text(_demo_config(_repo_contracts()), encoding="utf-8")
    return _run_lint_imports(tmp_path, config=config, pythonpath=tmp_path)


# --- Конфигурация ---


def test_pyproject_declares_required_contracts() -> None:
    by_name = {contract["name"]: contract for contract in _repo_contracts()}
    for name, (source_modules, forbidden_modules) in REQUIRED_CONTRACTS.items():
        assert name in by_name, f"в pyproject.toml нет контракта «{name}»"
        contract = by_name[name]
        assert contract["type"] == "forbidden"
        assert contract["source_modules"] == source_modules
        assert set(forbidden_modules) <= set(contract["forbidden_modules"])


def test_make_lint_runs_import_linter() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^lint:\n((?:\t.*\n)+)", makefile, flags=re.MULTILINE)
    assert match is not None, "в Makefile нет цели lint"
    assert "lint-imports" in match.group(1)


# --- Реальный репозиторий ---


def test_repository_respects_module_boundaries() -> None:
    result = _run_lint_imports(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout
    assert _broken_contracts(result.stdout) == set()


# --- Чувствительность контрактов на временном пакете ---

# Все нарушения собраны в одном дереве: один запуск lint-imports вместо семи. Каждое нарушение
# лежит в отдельном файле, поэтому в отчёте оно узнаётся по цепочке «импортёр -> импортируемый».
VIOLATION_FILES = {
    "demo/generation/direct.py": "import demo.storage\n",
    "demo/helpers/__init__.py": "import demo.extractors\n",
    "demo/generation/indirect.py": "import demo.helpers\n",
    "demo/validation/direct.py": "import demo.llm.prompts\n",
    # пакет llm реэкспортирует prompts, поэтому импорт пакета целиком тоже нарушение:
    # validation должна импортировать конкретные модули (например, llm.base)
    "demo/validation/package.py": "from demo import llm\n",
    "demo/forecast/to_llm.py": "import demo.llm\n",
    "demo/forecast/to_generation.py": "import demo.generation\n",
}

DETECTED_CHAINS = [
    pytest.param("demo.generation.direct -> demo.storage", id="generation-storage"),
    pytest.param("demo.generation.indirect -> demo.helpers", id="generation-extractors-indirect"),
    pytest.param("demo.helpers -> demo.extractors", id="generation-extractors-indirect-tail"),
    pytest.param("demo.validation.direct -> demo.llm.prompts", id="validation-prompts"),
    pytest.param("demo.validation.package -> demo.llm", id="validation-llm-package-indirect"),
    pytest.param("demo.forecast.to_llm -> demo.llm", id="forecast-llm"),
    pytest.param("demo.forecast.to_generation -> demo.generation", id="forecast-generation"),
]


@pytest.fixture(scope="module")
def violations_report(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    return _check_demo_tree(tmp_path_factory.mktemp("demo_violations"), VIOLATION_FILES)


def test_violations_fail_the_check(violations_report: subprocess.CompletedProcess[str]) -> None:
    assert violations_report.returncode != 0, violations_report.stdout


def test_every_contract_catches_its_violation(
    violations_report: subprocess.CompletedProcess[str],
) -> None:
    assert _broken_contracts(violations_report.stdout) == set(REQUIRED_CONTRACTS)


@pytest.mark.parametrize("chain", DETECTED_CHAINS)
def test_violation_is_reported(
    violations_report: subprocess.CompletedProcess[str], chain: str
) -> None:
    assert chain in violations_report.stdout


def test_allowed_imports_do_not_break_contracts(tmp_path: Path) -> None:
    result = _check_demo_tree(tmp_path, {})

    assert result.returncode == 0, result.stdout + result.stderr
    assert _broken_contracts(result.stdout) == set()
    assert f"Contracts: {len(REQUIRED_CONTRACTS)} kept, 0 broken" in result.stdout
