import typer

app = typer.Typer(help="Specialty coffee bean tracker.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Root command group; subcommands (scrape, db, runs) are added as they land."""
