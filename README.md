<div id="top" align="center">

# Cheapy

Agent-first MCP server and Python package for cheap flight search.

Máy chủ MCP và gói Python ưu tiên tác nhân AI để tìm vé máy bay giá rẻ.

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

Cheapy cung cấp một bề mặt tìm kiếm chuyến bay gọn nhẹ và thân thiện với tác
nhân AI: gói Python, CLI ưu tiên JSON, và MCP stdio server với công cụ
`search_cheapest_flights`. Dữ liệu nhà cung cấp được chuẩn hóa theo Contract V1
để agent đọc được giá vé, trạng thái provider, cảnh báo, lỗi, nhóm tiền tệ, và
kế hoạch tìm kiếm một cách ổn định.

<p align="right"><a href="#top">back to top</a></p>

## Features

| Capability | English | Tiếng Việt |
| --- | --- | --- |
| Contract V1 | Strict Pydantic models define request and response shapes for stable agent integrations. | Các model Pydantic chặt chẽ định nghĩa request và response để agent tích hợp ổn định. |
| MCP tool | `cheapy mcp` runs a protocol-clean stdio MCP server with `search_cheapest_flights`. | `cheapy mcp` chạy MCP server qua stdio và giữ stdout sạch cho protocol. |
| JSON-first CLI | CLI success payloads go to stdout as JSON; structured errors go to stderr. | CLI trả kết quả JSON trên stdout; lỗi có cấu trúc đi qua stderr. |
| Provider registry | Packaged providers include a deterministic fixture and a Google Fli live provider path. | Provider đóng gói gồm fixture cố định và đường live provider Google Fli. |
| Exact and expanded search | Supports exact requests and expanded flexible-date candidate planning. | Hỗ trợ tìm đúng ngày và lập kế hoạch ngày linh hoạt với `search_mode="expanded"`. |
| Offline default tests | Regular test commands avoid live provider calls unless explicitly enabled. | Test mặc định không gọi provider live trừ khi bật rõ ràng. |

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

Contract công khai nằm trong `cheapy/models/contracts.py`. Khi thay đổi field
request hoặc response, cần cập nhật ví dụ trong README theo file này.

<p align="right"><a href="#top">back to top</a></p>

## Getting Started

### Prerequisites

Cheapy requires Python 3.12 or newer and uses `uv` for dependency management.

Cheapy cần Python 3.12 trở lên và dùng `uv` để quản lý dependency.

```sh
python --version
uv --version
```

### Installation

Clone the repository and install development dependencies:

Clone repository và cài dependency phục vụ phát triển:

```sh
git clone https://github.com/chuanman2707/Cheapy.git
cd Cheapy
uv sync --extra dev
```

Verify the CLI is available:

Kiểm tra CLI đã chạy được:

```sh
uv run cheapy --version
```

Expected version:

Phiên bản kỳ vọng:

```text
0.1.0
```

<p align="right"><a href="#top">back to top</a></p>

## CLI Usage

Cheapy commands default to machine-readable output where practical.

Lệnh Cheapy ưu tiên output máy đọc được khi phù hợp.

```sh
uv run cheapy doctor
uv run cheapy providers list
uv run cheapy providers test
uv run cheapy schema
```

Human-readable health and provider reports are available with `--human`:

Có thể xem báo cáo để đọc bằng mắt người với `--human`:

```sh
uv run cheapy doctor --human
uv run cheapy providers test --human
```

Default provider checks do not run live provider calls. Live smoke checks are
opt-in:

Kiểm tra provider mặc định không gọi live provider. Smoke check live cần bật rõ
ràng:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live
```

<p align="right"><a href="#top">back to top</a></p>

## MCP Setup

Run the stdio MCP server directly:

Chạy trực tiếp MCP server qua stdio:

```sh
uv run cheapy mcp
```

Install the Cheapy MCP server into a supported local client:

Cài Cheapy MCP server vào client được hỗ trợ:

```sh
uv run cheapy mcp install --client codex
uv run cheapy mcp install --client claude
```

The MCP tool is `search_cheapest_flights`. Contract V1 requires
`schema_version="1"`, IATA airport codes, ISO dates, and optional passenger
counts.

MCP tool là `search_cheapest_flights`. Contract V1 yêu cầu
`schema_version="1"`, mã sân bay IATA, ngày ISO, và số lượng hành khách tùy
chọn.

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

Để lập kế hoạch ngày linh hoạt, đặt `search_mode` thành `expanded`. Với chuyến
khứ hồi, đặt `return_date` bằng ngày ISO `YYYY-MM-DD`.

<p align="right"><a href="#top">back to top</a></p>

## Testing

Run the full offline test suite:

Chạy toàn bộ test suite offline:

```sh
uv run pytest -v
```

Useful focused checks:

Một số lệnh test tập trung:

```sh
uv run pytest tests/test_contracts.py -v
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_schema_export.py -v
uv run pytest tests/test_mcp.py -v
```

Live provider tests are intentionally opt-in:

Test live provider được thiết kế để chỉ chạy khi bật rõ ràng:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_google_fli.py -v
```

<p align="right"><a href="#top">back to top</a></p>

## Contributing

Keep changes small, read the project instructions first, and use `uv` for all
package and test commands. Contract V1 models in `cheapy/models/contracts.py`
are the source of truth. Keep `cheapy mcp` stdout protocol-clean, with
diagnostics and errors on stderr.

Hãy giữ thay đổi gọn, đọc hướng dẫn của project trước, và dùng `uv` cho mọi
lệnh package/test. Model Contract V1 trong `cheapy/models/contracts.py` là
nguồn sự thật. `cheapy mcp` phải giữ stdout sạch cho protocol; diagnostic và
lỗi đi qua stderr.

Before publishing a change, run:

Trước khi publish thay đổi, chạy:

```sh
uv run cheapy providers test
uv run pytest -v
```

<p align="right"><a href="#top">back to top</a></p>
