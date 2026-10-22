from typer.testing import CliRunner

from beantracker.cli import app


def test_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "coffee bean tracker" in result.output.lower()
