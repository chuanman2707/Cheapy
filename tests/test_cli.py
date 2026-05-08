"""CLI baseline tests."""

import json

from typer.testing import CliRunner

from cheapy.cli import app


runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "0.1.0\n"


def test_doctor_prints_json_health_report_by_default(monkeypatch) -> None:
    monkeypatch.setattr("cheapy.cli.shutil.which", lambda name: "/tmp/cheapy")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "executable": "/tmp/cheapy",
        "status": "ok",
        "version": "0.1.0",
    }
    assert result.stderr == ""


def test_doctor_human_prints_success_health_report(monkeypatch) -> None:
    monkeypatch.setattr("cheapy.cli.shutil.which", lambda name: "/tmp/cheapy")

    result = runner.invoke(app, ["doctor", "--human"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Cheapy doctor\n"
        "version: 0.1.0\n"
        "executable: /tmp/cheapy\n"
        "status: ok\n"
    )
    assert result.stderr == ""


def test_doctor_reports_missing_executable_on_stderr(monkeypatch) -> None:
    monkeypatch.setattr("cheapy.cli.shutil.which", lambda name: None)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "MISSING_EXECUTABLE",
        "message": "cheapy executable was not found on PATH.",
        "suggestion": "Install Cheapy or add the cheapy executable to PATH.",
    }


def test_mcp_remains_outside_contract_foundation_gate() -> None:
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error == {
        "error": True,
        "code": "MCP_OUTSIDE_CONTRACT_GATE",
        "message": "MCP server is outside this contract foundation gate.",
        "suggestion": "Use contract commands such as 'cheapy schema' in this gate.",
    }
    assert "MCP server is outside this contract foundation gate" in error["message"]


def test_unknown_command_reports_json_usage_error() -> None:
    result = runner.invoke(app, ["bogus"])

    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error"] is True
    assert error["code"] == "USAGE_ERROR"
    assert "No such command" in error["message"]
    assert error["suggestion"] == "Run 'cheapy --help' for valid usage."
