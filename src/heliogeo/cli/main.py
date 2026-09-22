import typer

from heliogeo.version import __version__

app = typer.Typer(help="Heliogeo Assistant CLI")


@app.callback()
def main() -> None:
    """
    Heliogeo Assistant CLI.

    Этот callback нужен, чтобы Typer не делал единственную команду
    главной и требовал её имя (version).
    """
    pass


@app.command()
def version() -> None:
    """Показать версию."""
    typer.echo(f"heliogeo-assistant {__version__}")


if __name__ == "__main__":
    app()
