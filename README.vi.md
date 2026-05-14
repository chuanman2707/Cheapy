<div id="top" align="center">

<p>
  <a href="README.md">English</a> |
  <strong>Tiếng Việt</strong> |
  <a href="README.zh-CN.md">简体中文</a>
</p>

# Cheapy

Máy chủ MCP và gói Python ưu tiên tác nhân AI để tìm vé máy bay giá rẻ.

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9?style=flat-square&logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Typer](https://img.shields.io/badge/Typer-CLI-000000?style=flat-square&logo=typer&logoColor=white)](https://typer.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-Contract%20V1-E92063?style=flat-square&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Pytest](https://img.shields.io/badge/Pytest-tested-0A9EDC?style=flat-square&logo=pytest&logoColor=white)](https://docs.pytest.org/)

</div>

## Liên Kết Nhanh

- [Giới thiệu](#giới-thiệu)
- [Tính năng](#tính-năng)
- [Cấu trúc dự án](#cấu-trúc-dự-án)
- [Bắt đầu](#bắt-đầu)
- [Sử dụng CLI](#sử-dụng-cli)
- [Thiết lập MCP](#thiết-lập-mcp)
- [Kiểm thử](#kiểm-thử)
- [Ghi nhận](#ghi-nhận)
- [Đóng góp](#đóng-góp)

---

## Giới Thiệu

Cheapy cung cấp một bề mặt tìm kiếm chuyến bay gọn nhẹ và thân thiện với tác
nhân AI: gói Python, CLI ưu tiên JSON, và MCP stdio server với công cụ
`search_cheapest_flights`. Dữ liệu nhà cung cấp được chuẩn hóa theo Contract V1
để agent đọc được giá vé, trạng thái provider, cảnh báo, lỗi, nhóm tiền tệ, và
kế hoạch tìm kiếm một cách ổn định.

<p align="right"><a href="#top">về đầu trang</a></p>

## Tính Năng

| Khả năng | Chi tiết |
| --- | --- |
| Contract V1 | Các model Pydantic chặt chẽ định nghĩa request và response để agent tích hợp ổn định. |
| MCP tool | `cheapy mcp` chạy MCP server qua stdio và giữ stdout sạch cho protocol. |
| CLI ưu tiên JSON | Payload thành công đi qua stdout dưới dạng JSON; lỗi có cấu trúc đi qua stderr. |
| Provider registry | Provider đóng gói gồm fixture cố định và đường live provider Google Fli. |
| Tìm đúng ngày và mở rộng | Hỗ trợ tìm đúng ngày và lập kế hoạch ngày linh hoạt với `search_mode="expanded"`. |
| Test offline mặc định | Các lệnh test thông thường không gọi provider live trừ khi bật rõ ràng. |

<p align="right"><a href="#top">về đầu trang</a></p>

## Cấu Trúc Dự Án

```text
Cheapy/
├── cheapy/
│   ├── cli.py                  # Entrypoint CLI dùng Typer
│   ├── mcp.py                  # MCP stdio server và đăng ký tool
│   ├── mcp_installer.py        # Helper cài MCP cho Codex và Claude
│   ├── search.py               # Điều phối tìm kiếm và dựng response
│   ├── search_planner.py       # Lập kế hoạch candidate exact và expanded
│   ├── models/contracts.py     # Nguồn sự thật của Contract V1
│   ├── providers/              # Registry provider và adapter provider
│   └── data/                   # Dữ liệu sân bay và hub đóng gói
├── tests/                      # Test contract, CLI, MCP, provider, packaging
├── docs/superpowers/           # Ghi chú kế hoạch và thiết kế
├── pyproject.toml              # Metadata package và dependencies
└── uv.lock                     # Lockfile dependency tái lập bằng uv
```

Contract công khai nằm trong `cheapy/models/contracts.py`. Khi thay đổi field
request hoặc response, cần cập nhật ví dụ trong README theo file này.

<p align="right"><a href="#top">về đầu trang</a></p>

## Bắt Đầu

### Yêu Cầu

Cheapy cần Python 3.12 trở lên và dùng `uv` để quản lý dependency.

```sh
python --version
uv --version
```

### Cài Đặt

Để setup đơn giản nhất, paste prompt này vào Codex hoặc Claude:

```text
Hãy setup Cheapy từ GitHub trong môi trường này.

Yêu cầu:
- Không xóa hoặc ghi đè file có sẵn của user.
- Nếu thư mục Cheapy đã tồn tại, hãy dùng thư mục đó; nếu chưa có, clone https://github.com/chuanman2707/Cheapy.git.
- Đảm bảo uv đã có sẵn. Nếu thiếu uv và không thể cài đặt an toàn, dừng lại và báo rõ đang bị chặn ở đâu.
- Trong thư mục Cheapy, chạy uv sync --extra dev.
- Verify setup bằng:
  - uv run cheapy --version
  - uv run cheapy providers test
  - uv run pytest -v
- Nếu đang chạy trong Codex, chạy uv run cheapy mcp install --client codex.
- Nếu đang chạy trong Claude Code, chạy uv run cheapy mcp install --client claude.
- Không chạy live provider tests trừ khi tôi yêu cầu rõ.
- Báo lại các lệnh đã chạy và trạng thái cuối cùng.
```

Setup thủ công:

Clone repository và cài dependency phục vụ phát triển:

```sh
git clone https://github.com/chuanman2707/Cheapy.git
cd Cheapy
uv sync --extra dev
```

Kiểm tra CLI đã chạy được:

```sh
uv run cheapy --version
```

Phiên bản kỳ vọng:

```text
0.1.0
```

<p align="right"><a href="#top">về đầu trang</a></p>

## Sử Dụng CLI

Lệnh Cheapy ưu tiên output máy đọc được khi phù hợp.

```sh
uv run cheapy doctor
uv run cheapy providers list
uv run cheapy providers test
uv run cheapy schema
```

Có thể xem báo cáo để đọc bằng mắt người với `--human`:

```sh
uv run cheapy doctor --human
uv run cheapy providers test --human
```

Kiểm tra provider mặc định không gọi live provider. Smoke check live cần bật rõ
ràng:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live
```

<p align="right"><a href="#top">về đầu trang</a></p>

## Thiết Lập MCP

Chạy trực tiếp MCP server qua stdio:

```sh
uv run cheapy mcp
```

Cài Cheapy MCP server vào client được hỗ trợ:

```sh
uv run cheapy mcp install --client codex
uv run cheapy mcp install --client claude
```

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

Để lập kế hoạch ngày linh hoạt, đặt `search_mode` thành `expanded`. Với chuyến
khứ hồi, đặt `return_date` bằng ngày ISO `YYYY-MM-DD`.

<p align="right"><a href="#top">về đầu trang</a></p>

## Kiểm Thử

Chạy toàn bộ test suite offline:

```sh
uv run pytest -v
```

Một số lệnh test tập trung:

```sh
uv run pytest tests/test_contracts.py -v
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_schema_export.py -v
uv run pytest tests/test_mcp.py -v
```

Test live provider được thiết kế để chỉ chạy khi bật rõ ràng:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_google_fli.py -v
```

<p align="right"><a href="#top">về đầu trang</a></p>

## Ghi Nhận

Google Fli provider của Cheapy lấy cảm hứng từ và được xây trên project upstream
[Fli](https://punitarani.github.io/fli/), package được publish trên PyPI với tên
[`flights`](https://pypi.org/project/flights/). Fli cung cấp các primitive tìm
kiếm Google Flights mà Cheapy adapter lại vào provider layer, MCP tool, và định
dạng response Contract V1.

Phần credit cho nền tảng tích hợp Google Flights thuộc về project Fli và các
maintainer của họ. Cheapy bổ sung phần MCP ưu tiên agent, contract chặt chẽ,
normalization provider, installer flow, test, và tài liệu đa ngôn ngữ xung quanh
tích hợp đó.

<p align="right"><a href="#top">về đầu trang</a></p>

## Đóng Góp

Hãy giữ thay đổi gọn, đọc hướng dẫn của project trước, và dùng `uv` cho mọi
lệnh package/test. Model Contract V1 trong `cheapy/models/contracts.py` là
nguồn sự thật. `cheapy mcp` phải giữ stdout sạch cho protocol; diagnostic và
lỗi đi qua stderr.

Trước khi publish thay đổi, chạy:

```sh
uv run cheapy providers test
uv run pytest -v
```

<p align="right"><a href="#top">về đầu trang</a></p>
