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


def test_search_request_schema_documents_iata_only_airports() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    request_properties = exported["SearchRequestV1"]["properties"]
    origin_description = request_properties["origin"]["description"]
    destination_description = request_properties["destination"]["description"]

    assert "IATA" in origin_description
    assert "IATA" in destination_description
    assert "city" not in origin_description.lower()
    assert "city" not in destination_description.lower()


def test_schema_exports_local_storage_warning_code() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)
    assert "local_storage_failed" in json.dumps(exported["SearchResponseV1"])


def test_schema_exports_flight_offer_public_search_url() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    offer_properties = exported["SearchResponseV1"]["$defs"]["FlightOfferV1"][
        "properties"
    ]
    assert "public_search_url" in offer_properties
