<div id="top" align="center">

<p>
  <a href="README.md">English</a> |
  <a href="README.vi.md">Tiếng Việt</a> |
  <strong>简体中文</strong>
</p>

# Cheapy

面向 AI Agent 的 MCP 服务器和 Python 包，用于搜索低价航班。

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9?style=flat-square&logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Typer](https://img.shields.io/badge/Typer-CLI-000000?style=flat-square&logo=typer&logoColor=white)](https://typer.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-Contract%20V1-E92063?style=flat-square&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Pytest](https://img.shields.io/badge/Pytest-tested-0A9EDC?style=flat-square&logo=pytest&logoColor=white)](https://docs.pytest.org/)

</div>

## 快速链接

- [简介](#简介)
- [功能](#功能)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [CLI 用法](#cli-用法)
- [MCP 设置](#mcp-设置)
- [测试](#测试)
- [致谢](#致谢)
- [贡献](#贡献)

---

## 简介

Cheapy 提供一个轻量、适合 AI Agent 使用的航班搜索接口：Python 包、
以 JSON 为优先输出的 CLI，以及通过 stdio 运行的 MCP 服务器，并暴露
`search_cheapest_flights` 工具。它会把 provider 输出规范化为严格的
Contract V1 模型，让 agent 可以稳定解析航班报价、provider 状态、警告、
错误、货币分组和搜索计划元数据，而不需要抓取人类可读文本。

<p align="right"><a href="#top">返回顶部</a></p>

## 功能

| 能力 | 说明 |
| --- | --- |
| Contract V1 | 严格的 Pydantic 模型定义 request 和 response 结构，便于 agent 稳定集成。 |
| MCP 工具 | `cheapy mcp` 运行协议干净的 stdio MCP server，并提供 `search_cheapest_flights`。 |
| JSON 优先 CLI | 成功 payload 以 JSON 写入 stdout；结构化错误写入 stderr。 |
| Provider registry | 内置确定性的 fixture provider，以及 Google Fli live provider 路径。 |
| 精确与扩展搜索 | 支持精确请求，也支持灵活日期的 expanded candidate planning。 |
| 默认离线测试 | 常规测试命令不会调用 live provider，除非显式开启。 |

<p align="right"><a href="#top">返回顶部</a></p>

## 项目结构

```text
Cheapy/
├── cheapy/
│   ├── cli.py                  # Typer CLI 入口
│   ├── mcp.py                  # MCP stdio server 和 tool 注册
│   ├── mcp_installer.py        # Codex 和 Claude MCP 安装辅助
│   ├── search.py               # 搜索编排与 response 组装
│   ├── search_planner.py       # exact 和 expanded candidate planning
│   ├── models/contracts.py     # Contract V1 的事实来源
│   ├── providers/              # Provider registry 和 provider adapters
│   └── data/                   # 打包的机场和 hub 数据
├── tests/                      # Contract、CLI、MCP、provider、packaging 测试
├── docs/superpowers/           # 规划和设计说明
├── pyproject.toml              # Package metadata 和 dependencies
└── uv.lock                     # 可复现的 uv dependency lockfile
```

公开 contract 位于 `cheapy/models/contracts.py`。当 request 或 response
字段变化时，请同步更新 README 示例。

<p align="right"><a href="#top">返回顶部</a></p>

## 快速开始

### 前置条件

Cheapy 需要 Python 3.12 或更高版本，并使用 `uv` 管理依赖。

```sh
python --version
uv --version
```

### 安装

最简单的方式是把下面这个 prompt 粘贴到 Codex 或 Claude：

```text
请在这个环境中从 GitHub 设置 Cheapy。

要求：
- 不要删除或覆盖用户已有文件。
- 如果 Cheapy 目录已经存在，请使用它；否则 clone https://github.com/chuanman2707/Cheapy.git。
- 确保 uv 可用。如果缺少 uv 且无法安全安装，请停止并说明阻塞原因。
- 进入 Cheapy 目录后运行 uv sync --extra dev。
- 用以下命令验证 setup：
  - uv run cheapy --version
  - uv run cheapy providers test
  - uv run pytest -v
- 如果当前运行在 Codex 中，运行 uv run cheapy mcp install --client codex。
- 如果当前运行在 Claude Code 中，运行 uv run cheapy mcp install --client claude。
- 除非我明确要求，否则不要运行 live provider tests。
- 汇报你运行过的命令和最终状态。
```

手动设置：

克隆仓库并安装开发依赖：

```sh
git clone https://github.com/chuanman2707/Cheapy.git
cd Cheapy
uv sync --extra dev
```

验证 CLI 可用：

```sh
uv run cheapy --version
```

预期版本：

```text
0.1.0
```

<p align="right"><a href="#top">返回顶部</a></p>

## CLI 用法

在适合的场景下，Cheapy 命令默认输出机器可读内容。

```sh
uv run cheapy doctor
uv run cheapy providers list
uv run cheapy providers test
uv run cheapy schema
```

如需人类可读的 health 和 provider 报告，可以使用 `--human`：

```sh
uv run cheapy doctor --human
uv run cheapy providers test --human
```

默认 provider 检查不会调用 live provider。Live smoke check 需要显式开启：

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live
```

<p align="right"><a href="#top">返回顶部</a></p>

## MCP 设置

直接运行 stdio MCP server：

```sh
uv run cheapy mcp
```

把 Cheapy MCP server 安装到支持的本地客户端：

```sh
uv run cheapy mcp install --client codex
uv run cheapy mcp install --client claude
```

MCP 工具名是 `search_cheapest_flights`。Contract V1 要求
`schema_version="1"`、IATA 机场代码、ISO 日期，以及可选的乘客数量。

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

如需灵活日期 planning，请把 `search_mode` 设置为 `expanded`。往返搜索请把
`return_date` 设置为 ISO `YYYY-MM-DD` 日期。

<p align="right"><a href="#top">返回顶部</a></p>

## 测试

运行完整离线测试：

```sh
uv run pytest -v
```

常用的聚焦测试：

```sh
uv run pytest tests/test_contracts.py -v
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_schema_export.py -v
uv run pytest tests/test_mcp.py -v
```

Live provider 测试需要显式开启：

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_google_fli.py -v
```

<p align="right"><a href="#top">返回顶部</a></p>

## 致谢

Cheapy 的 Google Fli provider 受到上游
[Fli](https://punitarani.github.io/fli/) 项目的启发，并基于它构建；该项目在
PyPI 上以 [`flights`](https://pypi.org/project/flights/) 名称发布。Fli 提供
Google Flights 搜索 primitive，Cheapy 将其适配到 provider layer、MCP tool
以及 Contract V1 response 格式中。

底层 Google Flights 集成的 credit 属于 Fli 项目及其维护者。Cheapy 在此基础上
加入面向 agent 的 MCP 打包、严格 contract、provider normalization、installer
flow、测试和多语言文档。

<p align="right"><a href="#top">返回顶部</a></p>

## 贡献

请保持改动小而清晰，先阅读项目说明，并使用 `uv` 执行所有 package 和 test
命令。`cheapy/models/contracts.py` 中的 Contract V1 模型是事实来源。
`cheapy mcp` 必须保持 stdout 协议干净，diagnostics 和 errors 应写入 stderr。

发布改动前请运行：

```sh
uv run cheapy providers test
uv run pytest -v
```

<p align="right"><a href="#top">返回顶部</a></p>
