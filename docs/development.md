# 开发指南

## 环境

```bash
uv sync --extra dev        # 按 uv.lock 创建 .venv 并安装项目 + dev 依赖（--frozen 表示不更新锁文件）
```
> 注：uv.lock 以 pypi.org 为源；服务器直连 PyPI 慢时可改用镜像源重新 lock：
> `UV_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/ uv lock`，
> 或跳过锁文件用 `uv pip install -e ".[dev]"`。

## 测试

```bash
uv run pytest -q                              # 全部用例
uv run pytest tests/unit                      # 单元 + 安全边界
uv run pytest tests/integration/test_mcp_client.py   # MCP stdio 集成
uv run pytest tests/integration -m integration       # 真实 DB（需 Docker）
```

真实数据库集成测试由 `DB_ASSISTANT_TEST_*` 环境变量开关，未设置自动跳过；
`docker-compose.yml` 提供 PG 16 + MySQL 8。

### 服务器部署（已验证）

云服务器 `114.55.66.204`（Alibaba Cloud Linux 3）已完成部署验证：

- 项目位于 `/opt/db-assistant`，Python 3.11.13 + venv 依赖就绪
- Docker Compose 运行 PG16（`127.0.0.1:54329`）与 MySQL8（`127.0.0.1:33069`）
- 全量测试：**171 passed / 0 skipped**（真实 PG + MySQL 集成测试通过）

```bash
cd /opt/db-assistant
export DB_ASSISTANT_TEST_PG_HOST=127.0.0.1
export DB_ASSISTANT_TEST_PG_PORT=54329
export DB_ASSISTANT_TEST_PG_PASSWORD=test
export DB_ASSISTANT_TEST_MYSQL_PORT=33069
export DB_ASSISTANT_TEST_MYSQL_PASSWORD=test
.venv/bin/python -m pytest -q
```

## 演示数据

```bash
python scripts/seed_demo.py        # 重建 Docker PG/MySQL 演示库（users/products/orders/categories + JSON specs + 视图）
python scripts/seed_demo.py --skip-pg
```

演示库带表/列中文注释（PG `COMMENT ON ...`，MySQL DDL `COMMENT` 子句），
用于验证 schema 注释读取链路（`list_tables` / `table_schema` / `get_table_schema` 工具）。

## 代码检查

```bash
ruff check .
ruff format --check .
```

## 手工验收流程

```bash
export DB_ASSISTANT_CONFIG="$HOME/.config/db-assistant/config.toml"
db-assistant add postgres --name local-dev --host 127.0.0.1 --dbname app --user dev
db-assistant list
db-assistant test local-dev
python -m db_assistant_mcp            # stdio 模式，供 MCP 客户端接入
```

## 版本规划对照

本仓库当前完成 **v0.1 MVP**：6 个只读工具 + refresh_schema + ping、
安全网关（校验/限制/脱敏/审计）、CLI 连接管理、TOML 配置、schema 资源、
Prometheus 指标与健康检查。v0.2 起的语义层注入、translate_sql、webhook 审计、
SSH 隧道等见 [mcp_db.md](../mcp_db.md) 版本规划。
