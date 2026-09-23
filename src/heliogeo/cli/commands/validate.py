"""CLI-команда validate: проверка JSON по схеме UnifiedPayload."""

import json
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from heliogeo.core.logging import get_logger
from heliogeo.schema import UnifiedPayload

logger = get_logger(__name__)


def _load_json(path: str) -> dict[str, Any]:
    """Загрузить JSON из файла или stdin ('-')."""
    if path == "-":
        raw = sys.stdin.read()
    else:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not p.is_file():
            raise ValueError(f"Not a file: {path}")
        raw = p.read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Top-level JSON must be an object, got {type(data).__name__}")

    return data


def validate(
    path: str = typer.Argument(..., help="Путь к JSON или '-' для stdin"),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Вывести результат в JSON-формате",
    ),
) -> None:
    """Проверить JSON-файл на соответствие схеме UnifiedPayload."""
    logger.info("Validating payload from %s", path)

    try:
        data = _load_json(path)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Failed to load: %s", e)
        if output_json:
            typer.echo(json.dumps({"ok": False, "error": str(e)}))
        else:
            typer.secho(f"ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from e

    try:
        payload = UnifiedPayload.model_validate(data)
    except ValidationError as e:
        errors = e.errors()
        logger.error("Validation failed: %d errors", len(errors))
        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error_count": len(errors),
                        "errors": [
                            {
                                "loc": ".".join(str(x) for x in err["loc"]),
                                "msg": err["msg"],
                                "type": err["type"],
                            }
                            for err in errors
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.secho(f"INVALID: {len(errors)} error(s)", fg=typer.colors.RED)
            for err in errors:
                loc = ".".join(str(x) for x in err["loc"])
                typer.echo(f"  - {loc}: {err['msg']}")
        raise typer.Exit(code=1) from e

    logger.info("Validation OK")
    if output_json:
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "issued_utc": payload.metadata.issued_utc.isoformat(),
                    "generator_version": payload.metadata.generator_version,
                }
            )
        )
    else:
        typer.secho("OK", fg=typer.colors.GREEN)
        typer.echo(f"  issued_utc: {payload.metadata.issued_utc.isoformat()}")
        typer.echo(f"  generator_version: {payload.metadata.generator_version}")
