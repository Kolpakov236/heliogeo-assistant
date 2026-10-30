"""CLI-группа ``corpus``: сбор архива, индекс текстов, просмотр и поиск.

Системные часы здесь не читаются (правило проекта), поэтому период сбора задаётся явно: сутки
старше двух суток от «сейчас», чтобы источник не подмешивал live-данные и ответы были
воспроизводимы.
"""

import time
from datetime import UTC, date, datetime

import typer

from heliogeo.core.logging import get_logger
from heliogeo.corpus import Corpus, CorpusError, TextRecord, build_corpus
from heliogeo.extractors.swx.client import SwxClient
from heliogeo.functions.backfill import (
    DEFAULT_DELAY_S,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DayResult,
    ThrottledSink,
    backfill,
    days_between,
    normalize_days,
)
from heliogeo.mirror.raw import RawStore
from heliogeo.storage.factory import open_object_store
from heliogeo.storage.keys import Bucket

logger = get_logger(__name__)

corpus_app = typer.Typer(help="Корпус эталонных текстов NOAA: сбор архива, индекс, просмотр.")

_MAX_SHOWN = 20


def _parse_day(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            f"ожидается YYYY-MM-DD, получено {value!r}", param_hint=option
        ) from None


def _progress(result: DayResult) -> None:
    if result.ok:
        source = "из сырого слоя" if result.from_raw else "скачано"
        extra = f", замечаний: {len(result.notes)}" if result.notes else ""
        typer.echo(f"{result.day}  ok   {source}{extra}")
    else:
        typer.secho(f"{result.day}  FAIL {result.error}", fg=typer.colors.RED)


def _load_corpus() -> Corpus:
    try:
        return Corpus.load(open_object_store(Bucket.LAKE))
    except CorpusError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@corpus_app.command("backfill")
def backfill_cmd(
    start: str = typer.Option(..., "--from", help="Первые сутки, YYYY-MM-DD (UTC)"),
    end: str = typer.Option(..., "--to", help="Последние сутки включительно, YYYY-MM-DD (UTC)"),
    delay: float = typer.Option(
        DEFAULT_DELAY_S, "--delay", min=0.0, help="Пауза между запросами к источнику, секунд"
    ),
    max_failures: int = typer.Option(
        DEFAULT_MAX_CONSECUTIVE_FAILURES,
        "--max-failures",
        min=1,
        help="Остановиться после стольких подряд ошибок «источник недоступен»",
    ),
    normalize: bool = typer.Option(
        True, "--normalize/--no-normalize", help="Собрать из данных L1 (Parquet) в бакете lake"
    ),
    build: bool = typer.Option(
        True, "--build/--no-build", help="После скачивания собрать индекс корпуса текстов"
    ),
) -> None:
    """Скачать данные и тексты за период в локальное хранилище (повторный запуск безопасен)."""
    first, last = _parse_day(start, "--from"), _parse_day(end, "--to")
    try:
        days = days_between(first, last)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None

    raw_store = open_object_store(Bucket.RAW)
    lake_store = open_object_store(Bucket.LAKE)

    def client_factory(raw: RawStore) -> SwxClient:
        return SwxClient(raw_sink=ThrottledSink(raw.save_snapshot, delay, time.sleep))

    run_id = f"backfill/{first.isoformat()}_{last.isoformat()}"
    typer.echo(f"Бэкфилл {first} … {last}: {len(days)} суток, пауза {delay} с")
    report = backfill(
        days,
        raw_store,
        client_factory=client_factory,
        run_id=run_id,
        max_consecutive_failures=max_failures,
        on_day=_progress,
    )
    typer.echo(
        f"Скачано: {len(report.ok_days)} суток, с ошибкой: {len(report.failed_days)}, "
        f"замечаний: {report.notes_count}"
    )
    if report.stopped_early:
        typer.secho(
            "Остановлено: источник недоступен несколько раз подряд. Запустите команду позже, "
            "уже скачанное не будет скачиваться повторно.",
            fg=typer.colors.YELLOW,
        )

    if normalize and report.ok_days:
        typer.echo("Нормализация данных в L1 …")
        normalized = normalize_days(report.ok_days, raw_store, lake_store, run_id=run_id)
        typer.echo(f"L1: {len(normalized.ok_days)} суток, с ошибкой: {len(normalized.failed_days)}")
        for result in normalized.results:
            if not result.ok:
                typer.secho(f"  {result.day}: {result.error}", fg=typer.colors.RED)
    if build:
        _, built = build_corpus(raw_store, lake_store)
        typer.echo(
            f"Индекс корпуса: {built.records} выпусков {built.by_kind}, "
            f"пропущено {len(built.skipped)}"
        )
    if report.stopped_early:
        raise typer.Exit(code=1)


@corpus_app.command("build")
def build_cmd() -> None:
    """Пересобрать индекс корпуса из сырого слоя (без обращений к источнику)."""
    corpus, report = build_corpus(open_object_store(Bucket.RAW), open_object_store(Bucket.LAKE))
    typer.echo(
        f"Манифестов: {report.days_scanned}; выпусков в индексе: {report.records} {report.by_kind}; "
        f"копий: {report.duplicates}; пропущено: {len(report.skipped)}"
    )
    for key, reason in report.skipped[:_MAX_SHOWN]:
        typer.secho(f"  пропущено {key}: {reason}", fg=typer.colors.YELLOW)
    if len(report.skipped) > _MAX_SHOWN:
        typer.echo(f"  … и ещё {len(report.skipped) - _MAX_SHOWN}")
    typer.echo(f"Ожидаемых выпусков не хватает: {len(corpus.missing_issues())}")


@corpus_app.command("stats")
def stats_cmd(
    missing: bool = typer.Option(False, "--missing", help="Перечислить недостающие выпуски"),
) -> None:
    """Сводка по корпусу: сколько выпусков, период, пропуски."""
    corpus = _load_corpus()
    for key, value in corpus.stats().items():
        typer.echo(f"{key}: {value}")
    if missing:
        for day, issue in corpus.missing_issues()[:200]:
            typer.echo(f"  нет: {day} {issue}")


def _print_record(record: TextRecord, *, full: bool) -> None:
    typer.secho(
        f"{record.issued_utc:%Y-%m-%d %H:%MZ}  {record.kind} {record.cycle}  "
        f"описывает {record.covers_from:%Y-%m-%d %H:%M} … {record.covers_to:%Y-%m-%d %H:%M}",
        bold=True,
    )
    typer.echo(f"  данные нужны за сутки: {', '.join(d.isoformat() for d in record.data_days)}")
    for name, parts in record.sections.items():
        typer.secho(f"  [{name}]", fg=typer.colors.CYAN)
        for part, text in parts.items():
            if part == "text" and len(parts) > 1 and not full:
                continue
            typer.echo(f"    {part}: {' '.join(text.split())}")


@corpus_app.command("show")
def show_cmd(
    issued: str = typer.Argument(
        ..., help="Время выпуска, YYYY-MM-DDTHH:MM (UTC), например 2026-01-15T00:30"
    ),
    full: bool = typer.Option(False, "--full", help="Показывать и полный текст раздела"),
) -> None:
    """Показать один выпуск: разделы и сутки данных, которые нужны для пары."""
    try:
        moment = datetime.fromisoformat(issued).replace(tzinfo=UTC)
    except ValueError:
        raise typer.BadParameter(f"ожидается YYYY-MM-DDTHH:MM, получено {issued!r}") from None
    record = _load_corpus().get(moment)
    if record is None:
        typer.secho(
            f"выпуска на {moment:%Y-%m-%d %H:%M} нет в корпусе", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1)
    _print_record(record, full=full)


@corpus_app.command("search")
def search_cmd(
    pattern: str = typer.Argument(..., help="Регулярное выражение, например 'M-class'"),
    kind: str | None = typer.Option(None, "--kind", help="discussion или rsga"),
    limit: int = typer.Option(20, "--limit", min=1, help="Сколько строк показать"),
) -> None:
    """Найти строки текста по регулярному выражению во всём корпусе."""
    shown = 0
    for record, line in _load_corpus().search(pattern, kind=kind):
        typer.echo(f"{record.issued_utc:%Y-%m-%d %H:%M}  {record.kind:<10} {line}")
        shown += 1
        if shown >= limit:
            typer.echo(f"… показано {limit}, остальное скрыто (--limit)")
            break
    if shown == 0:
        typer.echo("ничего не найдено")
