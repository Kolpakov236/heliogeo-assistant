"""Спецификации воркфлоу (deploy/workflows): структура, повторы, защита от зацикливания.

Это проверка самой спецификации, без запуска в облаке: куда ведут переходы, нет ли шагов без
выхода, можно ли зациклиться, не выходят ли в состояние запуска данные вместо ключей. Запуск на
стенде эти тесты не заменяют.
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from heliogeo.pipeline.pipeline import ErrorClass

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "deploy" / "workflows"
SPEC_FILES = sorted(WORKFLOWS_DIR.glob("*.yaml"))
DAILY = WORKFLOWS_DIR / "daily.yaml"

# Коды ошибок YaWL, допустимые в errorList: закрытый перечень сервиса. Собственные классы
# (SOURCE_UNAVAILABLE и т.п.) сюда не попадают — по ним решают проверки ответа функции.
YAWL_ERROR_CODES = {
    "ALL",
    "STEP_DATA_LIMIT_EXCEEDED",
    "STEP_NO_CHOICE_MATCHED",
    "STEP_PERMISSION_DENIED",
    "STEP_TIMEOUT",
    "STEP_INVALID_OUTPUT",
    "STEP_INTERNAL",
    "STEP_INVALID_TEMPLATE_EXPRESSION",
    "STEP_FAIL",
    "STEP_FAILED_PRECONDITION",
    "STEP_INVALID_ARGUMENT",
    "STEP_QUOTA_EXCEEDED",
    "FUNCTION_CALL_INVALID_RESPONSE",
    "YDB_CALL_SERVICE_UNAVAILABLE",
}
_HTTP_CODE = re.compile(r"HTTP_CALL_\d{3}")

# Девять функций облака (десятая в квоте — запас, её заводят только по согласованию)
FUNCTIONS = {
    "INGEST",
    "NORMALIZE",
    "FEATURES",
    "FORECAST",
    "GENERATE",
    "VALIDATE",
    "RENDER_DELIVER",
    "VERIFY",
    "ADMIN",
}

STEP_TYPES = {"functionCall", "switch", "wait", "noop", "success", "fail"}
TERMINAL_TYPES = {"success", "fail"}
# Переход по условию, ограниченному числом попыток, или ремонтом «ровно один раз»
_GUARD = re.compile(r"\.attempt // \d+\) < \d+|\.repaired != true")

Edge = tuple[str, str, str | None]


def _load(path: Path) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(spec, dict)
    return spec


def _body(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Тип шага и его тело: в YaWL у шага ровно один ключ — тип."""
    assert len(step) == 1, f"у шага должен быть один ключ-тип, а не {list(step)}"
    kind, body = next(iter(step.items()))
    return kind, body or {}


def _edges(steps: dict[str, Any]) -> list[Edge]:
    """Все переходы: (откуда, куда, условие). У безусловных перехода условие — None."""
    edges: list[Edge] = []
    for name, step in steps.items():
        kind, body = _body(step)
        if kind in TERMINAL_TYPES:
            continue
        if kind == "switch":
            edges += [(name, c["next"], c["condition"]) for c in body["choices"]]
            if "default" in body:
                edges.append((name, body["default"], None))
            continue
        if "next" in body:
            edges.append((name, body["next"], None))
        edges += [(name, rule["next"], None) for rule in body.get("catch", [])]
    return edges


def _cycles(edges: list[Edge]) -> list[list[str | None]]:
    """Условия на рёбрах каждого простого цикла графа (граф небольшой, перебор достаточен)."""
    graph: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for src, dst, condition in edges:
        graph[src].append((dst, condition))
    order = {node: index for index, node in enumerate(sorted(graph))}
    found: list[list[str | None]] = []

    def walk(start: str, node: str, path: list[str], conditions: list[str | None]) -> None:
        for dst, condition in graph[node]:
            if dst == start:
                found.append([*conditions, condition])
            elif order.get(dst, -1) > order[start] and dst not in path:
                walk(start, dst, [*path, dst], [*conditions, condition])

    for start in graph:
        walk(start, start, [start], [])
    return found


def _templates(spec: dict[str, Any]) -> list[str]:
    """Все jq-шаблоны спецификации: input, output, condition, duration, errorMessage."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"input", "output", "condition", "duration", "errorMessage"} and (
                    isinstance(value, str)
                ):
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec["steps"])
    return found


def _error_lists(spec: dict[str, Any]) -> list[list[str]]:
    lists = [spec.get("defaultRetryPolicy", {}).get("errorList", [])]
    for step in spec["steps"].values():
        _, body = _body(step)
        lists.append(body.get("retryPolicy", {}).get("errorList", []))
        lists += [rule["errorList"] for rule in body.get("catch", [])]
    return [errors for errors in lists if errors]


@pytest.fixture(scope="module")
def daily() -> dict[str, Any]:
    return _load(DAILY)


def test_workflow_specs_exist() -> None:
    assert DAILY in SPEC_FILES


# --- Общая структура: проверяется для каждой спецификации в каталоге ---


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_spec_has_the_yawl_header(path: Path) -> None:
    spec = _load(path)
    assert spec["yawl"] == "0.1"
    assert spec["start"] in spec["steps"]


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_every_step_has_one_known_type(path: Path) -> None:
    for name, step in _load(path)["steps"].items():
        kind, _ = _body(step)
        assert kind in STEP_TYPES, f"шаг {name}: неизвестный тип {kind}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_transitions_lead_to_existing_steps(path: Path) -> None:
    steps = _load(path)["steps"]
    for src, dst, _ in _edges(steps):
        assert dst in steps, f"{src} → {dst}: такого шага нет"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_every_step_is_reachable_from_the_start(path: Path) -> None:
    spec = _load(path)
    graph: dict[str, set[str]] = defaultdict(set)
    for src, dst, _ in _edges(spec["steps"]):
        graph[src].add(dst)
    reached, pending = set(), [spec["start"]]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending += graph[node]

    assert set(spec["steps"]) - reached == set(), "есть недостижимые шаги"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_every_step_leads_somewhere(path: Path) -> None:
    """Шаг без выхода обрывает запуск молча: у каждого нетерминального шага есть переход."""
    steps = _load(path)["steps"]
    for name, step in steps.items():
        kind, body = _body(step)
        if kind in TERMINAL_TYPES:
            continue
        assert "next" in body or kind == "switch", f"у шага {name} нет перехода"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_every_switch_has_a_default(path: Path) -> None:
    """Switch без default роняет запуск ошибкой, если ни одно условие не подошло."""
    for name, step in _load(path)["steps"].items():
        kind, body = _body(step)
        if kind == "switch":
            assert body.get("default"), f"у Switch {name} нет default"
            assert body["choices"], f"у Switch {name} нет условий"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_the_run_can_end(path: Path) -> None:
    steps = _load(path)["steps"]
    kinds = {_body(step)[0] for step in steps.values()}
    assert "success" in kinds and "fail" in kinds


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_error_lists_use_only_codes_of_the_service(path: Path) -> None:
    """errorList принимает коды сервиса; собственные классы (SOURCE_UNAVAILABLE…) в нём не работают."""
    for errors in _error_lists(_load(path)):
        for code in errors:
            assert code in YAWL_ERROR_CODES or _HTTP_CODE.fullmatch(code), code


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_retry_policy_is_within_the_limits_of_the_service(path: Path) -> None:
    policy = _load(path).get("defaultRetryPolicy", {})
    assert policy.get("retryCount", 0) <= 100
    assert policy.get("errorListMode", "INCLUDE") in {"INCLUDE", "EXCLUDE"}
    for field in ("initialDelay", "maxDelay"):
        match = re.fullmatch(r"(\d+)([smh])", str(policy.get(field, "1s")))
        assert match, f"{field}: ожидается число с единицей s/m/h"
    max_delay = (
        int(policy.get("maxDelay", "1s")[:-1])
        * {"s": 1, "m": 60, "h": 3600}[str(policy.get("maxDelay", "1s"))[-1]]
    )
    assert max_delay <= 3600


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_jq_templates_do_not_clash_with_substitution_of_function_ids(path: Path) -> None:
    """Подстановка ${FN_*} (envsubst) не должна задевать шаблоны: в них нет знака доллара."""
    for template in _templates(_load(path)):
        assert "$" not in template, template


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_dollar_sign_is_only_in_function_placeholders(path: Path) -> None:
    """Файл подставляют через templatefile или envsubst: любая другая вставка с $ его сломает.

    Это касается и комментариев: `${FN_*}` в комментарии не разбирается как вставка.
    """
    text = path.read_text(encoding="utf-8")
    inserts = re.findall(r"\$\{?[^\s}]*\}?", text)
    assert inserts, "в спецификации нет ни одной заглушки функции"
    for insert in inserts:
        assert re.fullmatch(r"\$\{FN_[A-Z_]+\}", insert), f"лишняя вставка: {insert}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_function_ids_are_placeholders_for_the_nine_functions(path: Path) -> None:
    used: set[str] = set()
    for name, step in _load(path)["steps"].items():
        kind, body = _body(step)
        if kind == "functionCall":
            match = re.fullmatch(r"\$\{FN_([A-Z_]+)\}", body["functionId"])
            assert match, f"{name}: functionId должен быть заглушкой ${{FN_…}}"
            used.add(match.group(1))
    assert used <= FUNCTIONS, f"неизвестные функции: {used - FUNCTIONS}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_function_calls_pass_the_event_of_the_function(path: Path) -> None:
    """Вход функции — событие: run_id, date, attempt, in и cfg."""
    for name, step in _load(path)["steps"].items():
        kind, body = _body(step)
        if kind == "functionCall":
            for field in ("run_id", "date", "attempt", "in", "cfg"):
                assert f'"{field}"' in body["input"], f"{name}: во входе нет {field}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_state_keeps_pointers_not_data(path: Path) -> None:
    """В состояние запуска идут ключи и признаки результата, а не весь ответ функции."""
    allowed = {".ok", ".out", ".error", ".metrics.attempt", ".message"}
    for name, step in _load(path)["steps"].items():
        kind, body = _body(step)
        if kind != "functionCall":
            continue
        outputs = [body["output"], *(rule["output"] for rule in body.get("catch", []))]
        for output in outputs:
            no_strings = re.sub(r'"[^"]*"', '""', output)
            assert not re.search(
                r"[:(,\[]\s*\.(?![A-Za-z_])", no_strings
            ), f"{name}: в состояние копируется весь ответ: {output}"
            paths = set(re.findall(r"(?<![A-Za-z0-9_])\.[A-Za-z_][A-Za-z0-9_.]*", no_strings))
            assert paths <= allowed, f"{name}: лишнее в состоянии: {paths - allowed}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_every_cycle_is_bounded(path: Path) -> None:
    """Зацикливание невозможно: в каждом цикле есть условие с числом попыток или «ремонт один раз»."""
    cycles = _cycles(_edges(_load(path)["steps"]))
    for conditions in cycles:
        assert any(
            c is not None and _GUARD.search(c) for c in conditions
        ), f"цикл без ограничителя: {conditions}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
def test_pauses_grow_with_attempts_and_are_capped(path: Path) -> None:
    for name, step in _load(path)["steps"].items():
        kind, body = _body(step)
        if kind == "wait":
            assert re.search(r"\[\d+, 30 \* \.\w+\.attempt\] \| min", body["duration"]), name


# --- Суточный воркфлоу: решения, которые нельзя терять ---


def test_daily_runs_the_functions_in_the_agreed_order(daily: dict[str, Any]) -> None:
    steps = daily["steps"]
    calls = [name for name, step in steps.items() if _body(step)[0] == "functionCall"]
    assert calls[:7] == [
        "open_run",
        "ingest",
        "normalize",
        "features",
        "forecast",
        "generate",
        "validate",
    ]
    assert daily["start"] == "open_run"


def test_daily_uses_the_functions_of_the_agreed_set(daily: dict[str, Any]) -> None:
    """Из девяти функций суточный путь использует семь; verify и бэкфилл — в других воркфлоу."""
    ids = {
        body["functionId"]
        for step in daily["steps"].values()
        if (body := _body(step)[1]) and "functionId" in body
    }
    assert ids == {
        "${FN_ADMIN}",
        "${FN_INGEST}",
        "${FN_NORMALIZE}",
        "${FN_FEATURES}",
        "${FN_FORECAST}",
        "${FN_GENERATE}",
        "${FN_VALIDATE}",
        "${FN_RENDER_DELIVER}",
    }


def test_daily_takes_run_id_and_date_from_open_run(daily: dict[str, Any]) -> None:
    """Расписание запускает воркфлоу без входных данных: run_id и дату выдаёт open_run."""
    steps = daily["steps"]
    for name, step in steps.items():
        kind, body = _body(step)
        if kind != "functionCall" or name == "open_run":
            continue
        assert ".run.out.run_id" in body["input"], name
        assert ".run.out.date" in body["input"], name
        assert ".input.run_id" not in body["input"], name
        assert ".input.date" not in body["input"], name

    open_run = _body(steps["open_run"])[1]
    assert "(.input.run_id // null)" in open_run["input"]  # необязательны: переданные берутся
    assert "(.input.date // null)" in open_run["input"]
    assert _body(steps["open_run_result"])[1]["default"] == "fail_run"  # без run_id идти некуда


def test_daily_repair_happens_exactly_once(daily: dict[str, Any]) -> None:
    steps = daily["steps"]
    route = _body(steps["route"])[1]
    repair_choice = next(c for c in route["choices"] if c["next"] == "repair")
    assert ".repaired != true" in repair_choice["condition"]

    repair = _body(steps["repair"])[1]
    assert '"repaired": true' in repair["output"]
    # Флаг ставится и тогда, когда сама функция упала: второй попытки ремонта быть не должно
    assert all('"repaired": true' in rule["output"] for rule in repair["catch"])


def test_daily_failures_of_generation_and_validation_end_in_the_fallback(
    daily: dict[str, Any],
) -> None:
    steps = daily["steps"]
    for name in ("generate_result", "validate_result", "repair_result", "route"):
        assert _body(steps[name])[1]["default"] == "fallback", name

    fallback = _body(steps["fallback"])[1]
    assert '"mode": "template"' in fallback["input"]  # шаблонный текст, модель не участвует
    deliver_fallback = _body(steps["deliver_fallback"])[1]
    assert '"verdict": "fallback"' in deliver_fallback["input"]


def test_daily_platform_failures_of_model_steps_go_through_the_checks(
    daily: dict[str, Any],
) -> None:
    """Сбой платформы на шагах с моделью превращается в отказ шага и уходит в fallback."""
    for name in ("generate", "validate", "repair"):
        body = _body(daily["steps"][name])[1]
        assert [rule["next"] for rule in body["catch"]] == [f"{name}_result"]


def test_daily_source_outage_degrades_instead_of_failing(daily: dict[str, Any]) -> None:
    steps = daily["steps"]
    failed = _body(steps["ingest_failed"])[1]
    assert failed["choices"][0]["next"] == "normalize"
    assert "SOURCE_UNAVAILABLE" in failed["choices"][0]["condition"]
    # Настройки и внутренние ошибки деградацией не лечатся
    assert failed["default"] == "fail_run"
    assert "CONFIG_ERROR" not in failed["choices"][0]["condition"]
    assert "INTERNAL" not in failed["choices"][0]["condition"]


def test_daily_retries_only_what_the_function_marks_as_retryable(daily: dict[str, Any]) -> None:
    for name in ("ingest", "generate", "validate", "deliver", "deliver_fallback"):
        retry = _body(daily["steps"][f"{name}_result"])[1]["choices"][1]
        assert f".{name}.error.retryable == true" in retry["condition"]
        assert retry["next"] == f"{name}_pause"


def test_daily_computation_steps_are_not_retried_by_class(daily: dict[str, Any]) -> None:
    for name in ("normalize", "features", "forecast"):
        result = _body(daily["steps"][f"{name}_result"])[1]
        assert len(result["choices"]) == 1
        assert result["default"] == "fail_run"


def test_daily_error_classes_in_conditions_are_the_agreed_ones(daily: dict[str, Any]) -> None:
    names = {c.value for c in ErrorClass}
    used = {
        literal
        for template in _templates(daily)
        for literal in re.findall(r'"([A-Z][A-Z_]+)"', template)
    }
    assert used <= names, f"в спецификации неизвестные классы ошибок: {used - names}"


def test_daily_verdict_of_the_issue_matches_the_path(daily: dict[str, Any]) -> None:
    deliver = _body(daily["steps"]["deliver"])[1]["input"]
    assert '"repaired"' in deliver and '"pass"' in deliver
