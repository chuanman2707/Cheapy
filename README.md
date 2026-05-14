<div id="top" align="center">

<p>
  <strong>English</strong> |
  <a href="README.vi.md">Tiếng Việt</a> |
  <a href="README.zh-CN.md">简体中文</a>
</p>

# Cheapy

Agent-first MCP server and Python package for cheap flight search.

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9?style=flat-square&logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Typer](https://img.shields.io/badge/Typer-CLI-000000?style=flat-square&logo=typer&logoColor=white)](https://typer.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-Contract%20V1-E92063?style=flat-square&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Pytest](https://img.shields.io/badge/Pytest-tested-0A9EDC?style=flat-square&logo=pytest&logoColor=white)](https://docs.pytest.org/)

</div>

## Quick Links

- [Introduction](#introduction)
- [Features](#features)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [CLI Usage](#cli-usage)
- [MCP Setup](#mcp-setup)
- [Testing](#testing)
- [Contributing](#contributing)

---

## Introduction

Cheapy provides a small, agent-friendly flight-search surface: a Python package,
a JSON-first CLI, and an MCP stdio server exposing `search_cheapest_flights`.
It normalizes provider output into strict Contract V1 models so agents can parse
flight offers, provider statuses, warnings, errors, currency groups, and search
plan metadata without scraping human text.

<p align="right"><a href="#top">back to top</a></p>

## Features

| Capability | Details |
| --- | --- |
| Contract V1 | Strict Pydantic models define request and response shapes for stable agent integrations. |
| MCP tool | `cheapy mcp` runs a protocol-clean stdio MCP server with `search_cheapest_flights`. |
| JSON-first CLI | CLI success payloads go to stdout as JSON; structured errors go to stderr. |
| Provider registry | Packaged providers include a deterministic fixture and a Google Fli live provider path. |
| Exact and expanded search | Supports exact requests and expanded flexible-date candidate planning. |
| Offline default tests | Regular test commands avoid live provider calls unless explicitly enabled. |

<p align="right"><a href="#top">back to top</a></p>

## Project Structure

```text
Cheapy/
├── cheapy/
│   ├── cli.py                  # Typer CLI entrypoint
│   ├── mcp.py                  # MCP stdio server and tool registration
│   ├── mcp_installer.py        # Codex and Claude MCP installer helpers
│   ├── search.py               # Search orchestration and response assembly
│   ├── search_planner.py       # Exact and expanded candidate planning
│   ├── models/contracts.py     # Contract V1 source of truth
│   ├── providers/              # Provider registry and provider adapters
│   └── data/                   # Packaged airport and hub data
├── tests/                      # Contract, CLI, MCP, provider, and packaging tests
├── docs/superpowers/           # Planning and design notes
├── pyproject.toml              # Package metadata and dependencies
└── uv.lock                     # Reproducible uv dependency lockfile
```

The public contract lives in `cheapy/models/contracts.py`. Keep README examples
aligned with that file whenever request or response fields change.

<p align="right"><a href="#top">back to top</a></p>

## Getting Started

### Prerequisites

Cheapy requires Python 3.12 or newer and uses `uv` for dependency management.

```sh
python --version
uv --version
```

### Installation

Clone the repository and install development dependencies:

```sh
git clone https://github.com/chuanman2707/Cheapy.git
cd Cheapy
uv sync --extra dev
```

Verify the CLI is available:

```sh
uv run cheapy --version
```

Expected version:

```text
0.1.0
```

<p align="right"><a href="#top">back to top</a></p>

## CLI Usage

Cheapy commands default to machine-readable output where practical.

```sh
uv run cheapy doctor
uv run cheapy providers list
uv run cheapy providers test
uv run cheapy schema
```

Human-readable health and provider reports are available with `--human`:

```sh
uv run cheapy doctor --human
uv run cheapy providers test --human
```

Default provider checks do not run live provider calls. Live smoke checks are
opt-in:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live
```

<p align="right"><a href="#top">back to top</a></p>

## MCP Setup

Run the stdio MCP server directly:

```sh
uv run cheapy mcp
```

Install the Cheapy MCP server into a supported local client:

```sh
uv run cheapy mcp install --client codex
uv run cheapy mcp install --client claude
```

The MCP tool is `search_cheapest_flights`. Contract V1 requires
`schema_version="1"`, IATA airport codes, ISO dates, and optional passenger
counts.

```json
{
  "schema_version": "1",
  "origin": "CXR",
  "destination": "SGN",
  "departure_date": "2026-07-10",
  "return_date": null,
  "search_mode": "exact",
  "passengers": {
    "adults": 1,
    "children": 0,
    "infants_on_lap": 0,
    "infants_in_seat": 0
  },
  "max_results": 5
}
```

For flexible-date planning, set `search_mode` to `expanded`. For round trips,
set `return_date` to an ISO `YYYY-MM-DD` date.

<p align="right"><a href="#top">back to top</a></p>

## Testing

Run the full offline test suite:

```sh
uv run pytest -v
```

Useful focused checks:

```sh
uv run pytest tests/test_contracts.py -v
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_schema_export.py -v
uv run pytest tests/test_mcp.py -v
```

Live provider tests are intentionally opt-in:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_google_fli.py -v
```

<p align="right"><a href="#top">back to top</a></p>

## Contributing

Keep changes small, read the project instructions first, and use `uv` for all
package and test commands. Contract V1 models in `cheapy/models/contracts.py`
are the source of truth. Keep `cheapy mcp` stdout protocol-clean, with
diagnostics and errors on stderr.

Before publishing a change, run:

```sh
uv run cheapy providers test
uv run pytest -v
```

<p align="right"><a href="#top">back to top</a></p>
