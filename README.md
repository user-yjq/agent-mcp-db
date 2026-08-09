# db-assistant-mcp

MCP 数据库助手：为 Claude Code / Cursor / Codex 等 AI 编程工具提供
**PostgreSQL / MySQL** 的只读数据库能力。默认只读、强制安全边界、可审计。

## 安装

```bash
# 方式一：从 PyPI 安装（发布后）
pip install db-assistant-mcp

# 方式二：源码开发安装
uv pip install -e .

# 离线/内网：构建并安装 wheel
python scripts/build_wheel.py
pip install dist/db_assistant_mcp-*.whl
```

## 快速开始

```bash
export DB_ASSISTANT_CONFIG="$HOME/.config/db-assistant/config.toml"

# 管理连接（交互式输入密码，不回显）
db-assistant add postgres --name local-dev --host 127.0.0.1 --dbname app --user root --mode full
db-assistant list
db-assistant test local-dev

# 以 stdio 模式启动 MCP Server
python -m db_assistant_mcp

# 以 streamable HTTP 模式启动（远程共享部署，需先配置 [http] token_env）
# export DB_ASSISTANT_HTTP_TOKEN="$(openssl rand -hex 32)"
# python -m db_assistant_mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

在 Cursor / Claude Desktop 的 MCP 配置中加入：

```json
{
  "mcpServers": {
    "db-assistant": {
      "command": "db-assistant-mcp",
      "env": {
        "DB_ASSISTANT_CONFIG": "/path/to/config.toml"
      }
    }
  }
}
```

## 工具

| 工具 | 说明 |
|---|---|
| `list_databases` | 列出已配置的连接 |
| `list_tables` | 列出连接下的表/视图与行数估算（含注释与类型） |
| `get_table_schema` | 获取表结构：列/类型/主外键/索引/注释 |
| `search_schema` | 模糊搜索表/列（命中 glossary 中文语义词，含 pattern 展开） |
| `execute_query` | 只读执行 SELECT / WITH / EXPLAIN |
| `explain_query` | 查看执行计划（analyze / format: raw、tree、markdown） |
| `translate_sql` | SQL 方言转换（postgres ↔ mysql，产物只读回验） |
| `refresh_schema` | 主动失效 schema 缓存 |
| `ping` | 连接健康检查 |

资源：`db://{name}/schema`、`db://{name}/tables`、`db://{name}/semantic`。

## 安全设计

- 仅放行只读语句（SELECT / WITH / EXPLAIN / SHOW），基于 SQL 解析器（sqlglot）校验，解析失败即拒绝（fail-closed）
- 拒绝多语句、嵌套写入、事务控制、危险函数（pg_read_file、pg_sleep、LOAD_FILE 等）
- 行数上限 + 查询超时 + 并发限制
- 敏感列脱敏（`***`）与排除列/表隐藏
- 全量审计日志（file / stdout / webhook）
- 凭据仅通过环境变量或 AES-256-GCM 加密存储

## 文档

- [协议设计](mcp_db.md)
- [管理员手册](admin-guide.md)
- [开发进度](PROGRESS.md)
- [开发文档](docs/)
