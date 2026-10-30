"""CLI для Heliogeo Assistant."""

import logging

import typer

from heliogeo.cli.commands.corpus import corpus_app
from heliogeo.cli.commands.run import run as run_cmd
from heliogeo.cli.commands.validate import validate as validate_cmd
from heliogeo.core.config import get_settings
from heliogeo.core.logging import setup_logging
from heliogeo.version import __version__

app = typer.Typer(help="Heliogeo Assistant CLI")
logger = logging.getLogger(__name__)


@app.callback()
def main() -> None:
    """Heliogeo Assistant CLI."""
    settings = get_settings()
    setup_logging(level=settings.log_level)


@app.command()
def version() -> None:
    """Показать версию."""
    logger.info("version command invoked")
    typer.echo(f"heliogeo-assistant {__version__}")


app.command(name="validate")(validate_cmd)
app.command(name="run")(run_cmd)
app.add_typer(corpus_app, name="corpus")


if __name__ == "__main__":
    app()
