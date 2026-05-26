"""CLI baseline tests."""

import json
from typing import Any

from typer.testing import CliRunner

from cheapy.cli import app
from cheapy.mcp_installer import InstallerError
from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.registry import ProviderManifestError
from cheapy.storage import sqlite as storage


runner = CliRunner()


def _cli_request() -> SearchRequestV1:
    return SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "max_results": 5,
        }
    )


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "fixture:1",
        "price_amount": 1_280_000.0,
        "currency": "VND",
        "comparable": True,
        "rank_within_currency": 1,
        "global_rank": 1,
        "provider": "manual_fixture",
        "requested_origin": "CXR",
        "requested_destination": "SGN",
        "actual_origin": "CXR",
        "actual_destination": "SGN",
        "nearby_origin_distance_km": None,
        "nearby_destination_distance_km": None,
        "requested_departure_date": "2026-07-10",
        "actual_departure_date": "2026-07-10",
        "departure_offset_days": 0,
        "requested_return_date": None,
        "actual_return_date": None,
        "return_offset_days": None,
        "legs": [
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:25:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        "total_duration_minutes": 70,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _provider_status(**overrides: Any) -> ProviderStatusV1:
    data: dict[str, Any] = {
        "provider_name": "manual_fixture",
        "capability": "exact_one_way",
        "status": ProviderStatusCode.SUCCESS,
        "planned_call_count": 1,
        "executed_call_count": 1,
        "succeeded_call_count": 1,
        "failed_call_count": 0,
        "duration_ms": 12,
        "warnings": [],
        "errors": [],
        "retryable": False,
    }
    data.update(overrides)
    return ProviderStatusV1.model_validate(data)


def _cli_response(**overrides: Any) -> SearchResponseV1:
    offers = overrides.pop("offers", [_offer()])
    mixed_currency = overrides.pop(
        "mixed_currency", len({offer.currency for offer in offers}) > 1
    )
    data: dict[str, Any] = {
        "schema_version": "1",
        "status": SearchStatus.SUCCESS,
        "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
        "offers": offers,
        "warnings": [],
        "errors": [],
        "provider_statuses": [_provider_status()],
        "search_plan": SearchPlanV1(
            search_mode=SearchMode.EXACT,
            planned_candidate_count=1,
            executed_candidate_count=1,
            planned_provider_call_count=1,
            executed_provider_call_count=1,
            candidate_count_by_family={CandidateFamily.EXACT: 1},
            provider_call_count_by_family={CandidateFamily.EXACT: 1},
            truncated=False,
            truncated_families=[],
            candidate_families=[CandidateFamily.EXACT],
        ),
        "mixed_currency": mixed_currency,
        "currency_groups": [
            CurrencyGroupV1(
                currency=currency,
                offer_ids=[
                    offer.offer_id for offer in offers if offer.currency == currency
                ],
            )
            for currency in sorted({offer.currency for offer in offers})
        ],
        "currency_notes": [] if not mixed_currency else ["Currencies are not comparable."],
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)


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


def test_mcp_install_codex_prints_json(monkeypatch) -> None:
    def fake_install(client, *, project_root):
        assert client == "codex"
        return {
            "status": "ok",
            "client": "codex",
            "method": "official_cli",
        }

    monkeypatch.setattr("cheapy.cli.install_mcp", fake_install)

    result = runner.invoke(app, ["mcp", "install", "--client", "codex"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "status": "ok",
        "client": "codex",
        "method": "official_cli",
    }


def test_mcp_install_reports_installer_error(monkeypatch) -> None:
    error = InstallerError(
        code="MISSING_EXECUTABLE",
        message="cheapy executable was not found on PATH.",
        suggestion=(
            "Install the cheapy-flights package first, then ensure the cheapy "
            "executable is on PATH."
        ),
    )

    def fake_install(client, *, project_root):
        raise error

    monkeypatch.setattr("cheapy.cli.install_mcp", fake_install)

    result = runner.invoke(app, ["mcp", "install", "--client", "codex"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == json.dumps(error.payload(), sort_keys=True) + "\n"


def test_mcp_install_rejects_invalid_client() -> None:
    result = runner.invoke(app, ["mcp", "install", "--client", "vscode"])

    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error"] is True
    assert error["code"] == "USAGE_ERROR"
    assert "Invalid value for '--client'" in error["message"]
    assert error["suggestion"] == "Run 'cheapy --help' for valid usage."


def test_providers_list_prints_json() -> None:
    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert "skyscanner" not in providers
    assert payload["status"] == "ok"
    assert providers == {
        "google_fli": {
            "capabilities": ["exact_one_way", "exact_round_trip"],
            "default_enabled": True,
            "display_name": "Google Fli live provider",
            "enabled": True,
            "name": "google_fli",
            "provider_kind": "live",
        },
        "manual_fixture": {
            "capabilities": ["exact_one_way"],
            "default_enabled": True,
            "display_name": "Manual fixture provider",
            "enabled": True,
            "name": "manual_fixture",
            "provider_kind": "fixture",
        },
        "traveloka": {
            "capabilities": ["exact_one_way", "exact_round_trip"],
            "default_enabled": True,
            "display_name": "Traveloka research provider",
            "enabled": True,
            "name": "traveloka",
            "provider_kind": "live",
        },
    }


def test_providers_test_prints_json() -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert "skyscanner" not in providers
    assert payload["status"] == "ok"
    assert payload["providers_tested"] == 3
    assert providers["manual_fixture"]["status"] == "success"
    assert providers["manual_fixture"]["provider_kind"] == "fixture"
    assert providers["manual_fixture"]["live_smoke"] == "not_applicable"
    assert providers["google_fli"]["status"] == "skipped"
    assert providers["google_fli"]["provider_kind"] == "live"
    assert providers["google_fli"]["live_smoke"] == "not_run"
    assert providers["traveloka"]["status"] == "skipped"
    assert providers["traveloka"]["provider_kind"] == "live"
    assert providers["traveloka"]["live_smoke"] == "not_run"


def test_providers_test_default_does_not_run_live_provider(monkeypatch) -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert providers["manual_fixture"]["status"] == "success"
    assert providers["manual_fixture"]["live_smoke"] == "not_applicable"
    assert providers["google_fli"]["status"] == "skipped"
    assert providers["google_fli"]["live_smoke"] == "not_run"
    assert providers["traveloka"]["status"] == "skipped"
    assert providers["traveloka"]["provider_kind"] == "live"
    assert providers["traveloka"]["live_smoke"] == "not_run"


def test_providers_test_human_prints_success_report() -> None:
    result = runner.invoke(app, ["providers", "test", "--human"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "manual_fixture fixture exact_one_way: success" in result.stdout
    assert "google_fli live exact_one_way: skipped" in result.stdout
    assert "traveloka live exact_one_way: skipped" in result.stdout
    assert result.stdout.endswith("status: ok\n")


def test_providers_test_live_requires_environment_gate(monkeypatch) -> None:
    monkeypatch.delenv("CHEAPY_RUN_LIVE_TESTS", raising=False)

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "LIVE_TESTS_NOT_ENABLED",
        "message": "Live provider tests require CHEAPY_RUN_LIVE_TESTS=1.",
        "suggestion": "Set CHEAPY_RUN_LIVE_TESTS=1 and rerun 'cheapy providers test --live'.",
    }


def test_providers_test_live_reports_structured_provider_failure_without_crashing(
    monkeypatch,
) -> None:
    class FailingLiveProvider:
        name = "traveloka"
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
                        code=ErrorCode.PROVIDER_BLOCKED,
                        severity=Severity.ERROR,
                        message_en="Traveloka blocked the request.",
                        details={
                            "provider": "traveloka",
                            "capability": "exact_one_way",
                            "failure_type": "blocked",
                        },
                        retryable=False,
                    )
                ],
                duration_ms=1,
                retryable=False,
            )

    monkeypatch.setenv("CHEAPY_RUN_LIVE_TESTS", "1")
    monkeypatch.setattr("cheapy.cli.load_live_test_providers", lambda: [FailingLiveProvider()])

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    provider = payload["providers"][0]
    assert provider["name"] == "traveloka"
    assert provider["status"] == "failed"
    assert provider["error_count"] == 1
    assert provider["live_smoke"] == "run"


def test_providers_test_live_keeps_fixture_failures_failing(monkeypatch) -> None:
    class FailingFixtureProvider:
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
                        message_en="Fixture provider failed.",
                    )
                ],
                duration_ms=1,
                retryable=False,
            )

    monkeypatch.setenv("CHEAPY_RUN_LIVE_TESTS", "1")
    monkeypatch.setattr(
        "cheapy.cli.load_live_test_providers",
        lambda: [FailingFixtureProvider()],
    )

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_FAILED",
        "message": "One or more provider checks failed.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }


def test_providers_test_live_reports_unexpected_provider_exception(monkeypatch) -> None:
    class RaisingLiveProvider:
        name = "google_fli"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise RuntimeError("live provider exploded")

    monkeypatch.setenv("CHEAPY_RUN_LIVE_TESTS", "1")
    monkeypatch.setattr("cheapy.cli.load_live_test_providers", lambda: [RaisingLiveProvider()])

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_LIVE_TEST_ERROR",
        "message": "A live provider check raised an unexpected exception.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }


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


def test_history_list_prints_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        storage.insert_search_snapshot(conn, _cli_request(), _cli_response())

    result = runner.invoke(app, ["history", "list", "--limit", "5"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["runs"][0]["origin"] == "CXR"
    assert payload["runs"][0]["best_price_amount"] == 1_280_000.0


def test_history_show_prints_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _cli_request(), _cli_response())

    result = runner.invoke(app, ["history", "show", str(run_id)])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["search_run"]["id"] == run_id
    assert payload["provider_runs"][0]["provider_name"] == "manual_fixture"
    assert payload["offer_observations"][0]["offer_id"] == "fixture:1"
    assert payload["response"]["request_id"] == _cli_response().request_id


def test_history_show_reports_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    result = runner.invoke(app, ["history", "show", "999"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "HISTORY_RUN_NOT_FOUND",
        "message": "Search run was not found.",
        "suggestion": "Run 'cheapy history list' to see available search runs.",
    }


def test_history_commands_fail_when_storage_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")
    opened_database = False

    def fake_open_database():
        nonlocal opened_database
        opened_database = True
        raise AssertionError("storage should not open when disabled")

    monkeypatch.setattr("cheapy.cli.storage.open_database", fake_open_database)

    for command in (["history", "list"], ["history", "show", "1"]):
        result = runner.invoke(app, command)

        assert result.exit_code == 1
        assert result.stdout == ""
        assert json.loads(result.stderr)["code"] == "STORAGE_DISABLED"
    assert opened_database is False
