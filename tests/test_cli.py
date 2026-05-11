"""CLI baseline tests."""

import json

from typer.testing import CliRunner

from cheapy.cli import app
from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.registry import ProviderManifestError


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


def test_unknown_command_reports_json_usage_error() -> None:
    result = runner.invoke(app, ["bogus"])

    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error"] is True
    assert error["code"] == "USAGE_ERROR"
    assert "No such command" in error["message"]
    assert error["suggestion"] == "Run 'cheapy --help' for valid usage."


def test_providers_list_prints_json() -> None:
    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "providers": [
            {
                "capabilities": ["exact_one_way"],
                "default_enabled": True,
                "display_name": "Manual fixture provider",
                "enabled": True,
                "name": "manual_fixture",
            }
        ],
        "status": "ok",
    }


def test_providers_test_prints_json() -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "providers": [
            {
                "capability": "exact_one_way",
                "error_count": 0,
                "name": "manual_fixture",
                "offer_count": 2,
                "status": "success",
            }
        ],
        "providers_tested": 1,
        "status": "ok",
    }


def test_providers_test_human_prints_success_report() -> None:
    result = runner.invoke(app, ["providers", "test", "--human"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == (
        "Cheapy providers test\n"
        "manual_fixture exact_one_way: success (offers: 2, errors: 0)\n"
        "status: ok\n"
    )


def test_providers_test_human_prints_failure_report(monkeypatch) -> None:
    class FailingProvider:
        name = "manual_fixture"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            return ProviderResult(
                provider_name=self.name,
                capability="exact_one_way",
                status=ProviderStatusCode.FAILED,
                offers=[],
                warnings=[],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="Provider fixture failed.",
                    )
                ],
                duration_ms=0,
                retryable=False,
            )

    monkeypatch.setattr("cheapy.cli.load_enabled_providers", lambda: [FailingProvider()])

    result = runner.invoke(app, ["providers", "test", "--human"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_FAILED",
        "message": "One or more provider checks failed.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }


def test_providers_list_reports_no_manifests(monkeypatch) -> None:
    monkeypatch.setattr("cheapy.cli.discover_provider_manifests", lambda: [])

    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "NO_PROVIDER_AVAILABLE",
        "message": "No packaged Cheapy providers were found.",
        "suggestion": "Reinstall Cheapy and verify package data is present.",
    }


def test_providers_list_reports_invalid_manifest(monkeypatch) -> None:
    def raise_manifest_error() -> None:
        raise ProviderManifestError("Invalid provider manifest for 'broken'")

    monkeypatch.setattr("cheapy.cli.discover_provider_manifests", raise_manifest_error)

    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_MANIFEST_INVALID",
        "message": "Invalid provider manifest for 'broken'",
        "suggestion": "Reinstall Cheapy and verify provider package data is valid.",
    }


def test_providers_test_reports_provider_level_failure(monkeypatch) -> None:
    class FailingProvider:
        name = "failing_provider"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            return ProviderResult(
                provider_name=self.name,
                capability="exact_one_way",
                status=ProviderStatusCode.FAILED,
                offers=[],
                warnings=[],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="Provider fixture failed.",
                    )
                ],
                duration_ms=0,
                retryable=False,
            )

    monkeypatch.setattr("cheapy.cli.load_enabled_providers", lambda: [FailingProvider()])

    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_FAILED",
        "message": "One or more provider checks failed.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }


def test_providers_test_reports_unexpected_provider_exception(monkeypatch) -> None:
    class RaisingProvider:
        name = "raising_provider"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise RuntimeError("provider exploded")

    monkeypatch.setattr("cheapy.cli.load_enabled_providers", lambda: [RaisingProvider()])

    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_ERROR",
        "message": "A provider check raised an unexpected exception.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }
