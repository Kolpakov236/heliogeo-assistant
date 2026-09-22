"""CLI для Heliogeo Assistant."""

import logging

import typer

from heliogeo.core.config import get_settings
from heliogeo.core.logging import setup_logging
from heliogeo.version import __version__

app = typer.Typer(help="Heliogeo Assistant CLI")
logger = logging.getLogger(__name__)


@app.callback()
def main() -> None:
    """Heliogeo Assistant CLI.

    Callback нужен, чтобы Typer не делал единственную команду главной.
    """
    settings = get_settings()
    setup_logging(level=settings.log_level)


@app.command()
def version() -> None:
    """Показать версию."""
    logger.info("version command invoked")
    typer.echo(f"heliogeo-assistant {__version__}")


if __name__ == "__main__":
    app()
