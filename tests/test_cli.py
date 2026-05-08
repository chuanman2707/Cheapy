"""CLI baseline tests."""

from typer.testing import CliRunner

from cheapy.cli import app


runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "0.1.0\n"


def test_doctor_prints_baseline_health_report() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code in {0, 1}
    assert (
        "Cheapy doctor" in result.stdout
        or "cheapy executable was not found" in result.stdout
        or "cheapy executable was not found" in result.stderr
    )


def test_mcp_remains_outside_contract_foundation_gate() -> None:
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "MCP server is outside this contract foundation gate" in result.stderr
