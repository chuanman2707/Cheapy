"""Schema export CLI tests."""

import json

from typer.testing import CliRunner

from cheapy.cli import app


runner = CliRunner()


def test_schema_exports_search_contract_schemas() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    assert "SearchRequestV1" in exported
    assert "SearchResponseV1" in exported
    assert exported["SearchRequestV1"]["type"] == "object"
    assert exported["SearchResponseV1"]["type"] == "object"


def test_schema_requires_always_present_response_fields() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    required = set(exported["SearchResponseV1"]["required"])
    assert {
        "warnings",
        "errors",
        "provider_statuses",
        "currency_groups",
        "currency_notes",
        "candidates",
    }.issubset(required)


def test_schema_describes_key_public_fields() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    request_properties = exported["SearchRequestV1"]["properties"]
    response_properties = exported["SearchResponseV1"]["properties"]

    for field_name in [
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "search_mode",
        "passengers",
        "max_results",
    ]:
        assert request_properties[field_name]["description"]

    for field_name in [
        "offers",
        "warnings",
        "errors",
        "provider_statuses",
        "search_plan",
        "mixed_currency",
        "currency_groups",
        "currency_notes",
        "candidates",
    ]:
        assert response_properties[field_name]["description"]
