"""CLI-команда run: полный pipeline."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from heliogeo.core.logging import get_logger
from heliogeo.delivery import get_delivery
from heliogeo.llm import BaseLLMProvider, MockLLMProvider
from heliogeo.llm.generator import GenerationResult
from heliogeo.pipeline.pipeline import run_pipeline
from heliogeo.products.base import Product, ProductMetadata
from heliogeo.render import render_product

logger = get_logger(__name__)

# YYYY-MM-DD или YYYY-MM-DDTHH:MM[:SS] с необязательным Z или смещением.
_DATE_ARGUMENT = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Ближе этого срока к «сейчас» источник подмешивает live-данные SWPC.
_LIVE_MIXING_WINDOW = timedelta(days=2)


def run(
    product: list[str] = typer.Option(
        ["icao_brief"],
        "--product",
        "-p",
        help="Продукт(ы): icao_brief, swpc_discussion, all",
    ),
    provider: str = typer.Option(
        "mock",
        "--provider",
        help="LLM-провайдер: mock | alice",
    ),
    deliver: str | None = typer.Option(
        None,
        "--deliver",
        "-d",
        help="Канал доставки: telegram | mock | none",
    ),
    formats: list[str] = typer.Option(
        ["txt"],
        "--format",
        "-f",
        help="Форматы: txt, pdf",
    ),
    output_dir: str = typer.Option(
        "outputs",
        "--output",
        "-o",
        help="Папка для сохранения файлов",
    ),
    fail_on_grounding: bool = typer.Option(
        False,
        "--fail-on-grounding",
        help="Прервать pipeline при провале grounding",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        help=(
            "Момент данных, UTC: YYYY-MM-DD (12:00 UTC, как в API) или YYYY-MM-DDTHH:MM. "
            "По умолчанию — сейчас."
        ),
    ),
    no_live: bool = typer.Option(
        False,
        "--no-live",
        help=(
            "Не подмешивать live-данные SWPC. Для воспроизводимых прогонов по архиву "
            "(даты старше двух суток)."
        ),
    ),
) -> None:
    """Прогнать полный pipeline: extract → LLM → render → deliver."""
    products = ["icao_brief", "swpc_discussion"] if "all" in product else product
    query_time = _parse_query_time(date)
    _warn_if_live_data_may_be_mixed(query_time, no_live)

    logger.info(
        "Running pipeline: products=%s, provider=%s, query_time=%s, no_live=%s",
        products,
        provider,
        query_time,
        no_live,
    )

    llm_provider = _get_provider(provider)

    result = run_pipeline(
        products=products,
        provider=llm_provider,
        fail_on_grounding=fail_on_grounding,
        query_time=query_time,
        no_live=no_live,
    )

    if not result.ok:
        typer.secho("Pipeline: FAILED", fg=typer.colors.RED)
        for step in result.steps:
            if not step.ok:
                typer.echo(f"  {step.name}: {step.error}")
        raise typer.Exit(code=1)

    typer.secho("Pipeline: OK", fg=typer.colors.GREEN)
    typer.echo(result.summary())

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    saved_files: list[Path] = []
    for name, gen_result in result.products.items():
        if not gen_result.ok:
            typer.secho(f"Skip {name}: grounding failed", fg=typer.colors.YELLOW)
            continue

        product_obj = _to_product(name, gen_result)
        for fmt in formats:
            data = render_product(product_obj, fmt=fmt)  # type: ignore[arg-type]
            file_path = output_path / f"{name}.{fmt}"
            file_path.write_bytes(data)
            saved_files.append(file_path)
            typer.echo(f"  Saved: {file_path} ({len(data)} bytes)")

    if deliver and deliver != "none":
        _deliver_products(result.products, deliver, formats)

    typer.secho(
        f"Done. {len(saved_files)} file(s) saved.",
        fg=typer.colors.GREEN,
    )


def _parse_query_time(value: str | None) -> datetime | None:
    """Момент из ``--date`` в UTC.

    ``YYYY-MM-DD`` означает 12:00 UTC, как в API; ``YYYY-MM-DDTHH:MM`` — указанное время (UTC,
    если пояс не указан). ``None`` — «сейчас».
    """
    if value is None:
        return None
    if _DATE_ARGUMENT.fullmatch(value) is None:
        raise typer.BadParameter(
            f"ожидается YYYY-MM-DD или YYYY-MM-DDTHH:MM (UTC), получено {value!r}",
            param_hint="--date",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            f"не календарная дата или время: {value!r}", param_hint="--date"
        ) from None
    if _DATE_ONLY.fullmatch(value):
        parsed = parsed.replace(hour=12)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _warn_if_live_data_may_be_mixed(query_time: datetime | None, no_live: bool) -> None:
    """Предупредить: ближе двух суток к «сейчас» без --no-live источник подмешивает live-данные."""
    if query_time is None or no_live:
        return
    if datetime.now(UTC) - query_time < _LIVE_MIXING_WINDOW:
        typer.secho(
            "Warning: --date is within the last 2 days, so live SWPC data may be mixed in and "
            "the run is not reproducible; add --no-live for archive replays.",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _get_provider(name: str) -> BaseLLMProvider:
    """Получить LLM-провайдер по имени."""
    if name == "mock":
        return MockLLMProvider(response="No data available.")
    if name == "alice":
        from heliogeo.llm.providers.alice import AliceLLMProvider

        return AliceLLMProvider()
    raise typer.BadParameter(f"Unknown provider: {name!r}")


def _to_product(name: str, gen_result: GenerationResult) -> Product:
    """Преобразовать GenerationResult в Product."""
    return Product(
        metadata=ProductMetadata(
            product_type=name,
            issued_utc=datetime.now(UTC),
            grounding_ok=gen_result.ok,
            grounding_summary=gen_result.grounding.summary(),
        ),
        text=gen_result.text,
    )


def _deliver_products(
    products: dict[str, GenerationResult],
    deliver_name: str,
    formats: list[str],
) -> None:
    """Отправить продукты через указанный канал."""
    try:
        delivery = get_delivery(deliver_name)
    except ValueError as e:
        typer.secho(f"Delivery error: {e}", fg=typer.colors.RED)
        return

    for name, gen_result in products.items():
        if not gen_result.ok:
            continue
        product_obj = _to_product(name, gen_result)
        try:
            delivery.send(product_obj, formats=formats)
            typer.echo(f"  Delivered via {deliver_name}: {name}")
        except Exception as e:
            logger.exception("Delivery failed for %s", name)
            typer.secho(
                f"  Delivery failed: {name} — {e}",
                fg=typer.colors.RED,
            )
