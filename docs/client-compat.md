# 客户端兼容性

db-assistant-mcp 遵循 MCP 标准协议（stdio / streamable HTTP 双传输），
所有支持 MCP 的客户端均可接入。本文档记录各客户端的接入方式、实测状态与注意点。

## 接入方式总览

| 客户端 | 传输 | 配置位置 | 实测状态 |
|---|---|---|---|
| Codex（OpenAI Codex CLI） | stdio | `~/.codex/config.toml` | ✅ 本仓库开发环境实测（2026-08） |
| Cursor | stdio | `.cursor/mcp.json`（项目级）或全局 | ⚠️ 标准配置，建议按文末清单实测 |
| Claude Desktop / Claude Code | stdio | `claude_desktop_config.json` / `~/.claude.json` | ⚠️ 标准配置，建议按文末清单实测 |
| 任意 MCP 客户端（远程） | streamable HTTP | 各客户端远程 MCP 配置 | ✅ 服务端内置（需 token，见下文） |

> 版本参考：`mcp` SDK 1.29.0、Python >= 3.11。客户端只要求支持 MCP 协议，
> 与 db-assistant 版本无关；本仓库 CI 在 Python 3.11 / 3.12 下全量验证。

## 通用准备

1. **安装**：`uv pip install -e .`（开发）或 `pip install dist/db_assistant_mcp-*.whl`（离线）；
   确认 `db-assistant-mcp --version` 可运行。
2. **服务端配置**：准备一份 `config.toml`（参考 `config/config.example.toml`），
   通过 `db-assistant add` 管理连接，或手写后 `db-assistant doctor` 校验。
3. **密码走环境变量**：连接密码通过 `password_env` 引用环境变量，不要写进 MCP 客户端配置；
   `DB_ASSISTANT_CONFIG` 指向你的 `config.toml`。

## Codex

在 `~/.codex/config.toml` 中注册（env 用子表 `[mcp_servers.<name>.env]`）：

```toml
[mcp_servers.db-assistant]
command = "/opt/db-assistant/.venv/bin/db-assistant-mcp"

[mcp_servers.db-assistant.env]
DB_ASSISTANT_CONFIG = "/opt/db-assistant/config/codex-local.toml"
DB_ASSISTANT_CODEX_PG_PASSWORD = "test"
```

- `command` 用绝对路径（如上）或安装到 PATH 后用 `db-assistant-mcp`。
- 本仓库的 `config/codex-local.toml` 就是一份可直接用的服务端配置示例
  （连 Docker 测试库，见 `docker-compose.yml`）。
- 启动 Codex 后直接问"列出数据库连接"，应返回 `list_databases` 结果。

**验收记录（2026-08-13，Codex 内实测）**：9/9 工具调用成功，全流程
（list_databases → search_schema → get_table_schema → execute_query → explain_query）
在 PG/MySQL 双库通过；`DROP TABLE` 返回 `SECURITY_REJECTED` 且审计记录
`allowed=false`；`users.phone/email` 返回 `***`（schema 标注 `masked: true`）。

## Cursor

项目根目录 `.cursor/mcp.json`（或全局配置）:

```json
{
  "mcpServers": {
    "db-assistant": {
      "command": "/opt/db-assistant/.venv/bin/db-assistant-mcp",
      "env": {
        "DB_ASSISTANT_CONFIG": "/opt/db-assistant/config/codex-local.toml",
        "DB_ASSISTANT_CODEX_PG_PASSWORD": "test"
      }
    }
  }
}
```

- 项目级 `.cursor/mcp.json` 优先级高于全局，改动后需重启 Cursor 生效。
- 完整可粘贴文件见 `config/examples/cursor-mcp.json`。

## Claude Desktop / Claude Code

- Claude Desktop（macOS）：
  `~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Code：`claude mcp add` 或编辑 `~/.claude.json`。

```json
{
  "mcpServers": {
    "db-assistant": {
      "command": "/opt/db-assistant/.venv/bin/db-assistant-mcp",
      "env": {
        "DB_ASSISTANT_CONFIG": "/opt/db-assistant/config/codex-local.toml"
      }
    }
  }
}
```

- 完整可粘贴文件见 `config/examples/claude-desktop.json`。

## 远程共享（streamable HTTP）

当有**多个客户端/多人**需要共享同一数据库访问时，用 HTTP 模式部署：

```bash
export DB_ASSISTANT_HTTP_TOKEN="$(openssl rand -hex 32)"
python -m db_assistant_mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

- 必须配置 `[http] token_env`，否则拒绝启动（fail-closed）；生产环境务必用 TLS 反向代理
  （见 `admin-guide.md` 3.6）。
- 客户端侧注册远程 MCP（示例，Cursor/Claude 均支持）：

```json
{
  "mcpServers": {
    "db-assistant": {
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer <DB_ASSISTANT_HTTP_TOKEN>" }
    }
  }
}
```

- 验证：`curl -H "Authorization: Bearer $TOKEN" https://mcp.example.com/healthz`
  应返回 healthy。

## 验收清单（新客户端接入时按此过一遍）

1. `db-assistant-mcp --version` 输出版本号。
2. 客户端能列出工具（`list_databases` / `list_tables` / `get_table_schema` /
   `search_schema` / `execute_query` / `explain_query` / `translate_sql` / `refresh_schema` / `ping`）。
3. 走通全流程：`list_databases` → `search_schema` → `get_table_schema` → `execute_query` → `explain_query`。
4. 触发一次安全拒绝（如 `DROP TABLE x`），确认返回"仅允许只读查询"且审计日志记录 `allowed=false`。
5. 查询含脱敏列（如 `phone`），确认返回 `***`。
6. stdio 模式下 `audit.log` 正常写入，且 stdout 未被审计日志污染（JSON-RPC 协议流正常）。

## 常见问题

- **stdio 下审计输出到 stdout 会污染协议流**：服务端已自动将 stdout 降级为 stderr，
  无需客户端处理。
- **连接时报"找不到配置文件"**：确认 `DB_ASSISTANT_CONFIG` 已注入客户端 env；
  未设置时默认读 `~/.config/db-assistant/config.toml`。
- **连接报"环境变量不存在"**：`password_env` 引用的变量必须在客户端 env 中注入，
  不要写在 `config.toml` 明文里。
- **HTTP 模式启动即退出**：`[http] token_env` 未配置或环境变量为空（fail-closed 设计）。
- **工具返回慢**：检查 `default_limit` / `query_timeout_sec` / `max_concurrent`
  （`config.example.toml` 有注释）。
