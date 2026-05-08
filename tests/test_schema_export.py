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
