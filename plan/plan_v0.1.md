# v0.1 MVP 实施计划

## 范围

- 技术栈：Python 3.11+ / FastMCP / asyncpg / aiomysql / sqlglot
- 交付：MCP Server（stdio）+ 8 个工具 + 3 类资源 + CLI + 安全网关 + 可观测性

## 阶段与任务（对照 PROGRESS.md）

| 阶段 | 任务 | 实现文件 |
|---|---|---|
| Phase 1 | T-1.1 脚手架 / T-1.2 配置解析 / T-1.3 日志与错误 | `pyproject.toml`、`config.py`、`errors.py`、`logging_utils.py` |
| Phase 2 | T-2.1 Server 骨架 / T-2.2 工具注册 / T-2.3 资源 | `server.py`、`tools/`、`resources/` |
| Phase 3 | T-3.1 PG / T-3.2 MySQL / T-3.3 连接池 | `drivers/postgres.py`、`drivers/mysql.py`、`drivers/pool.py` |
| Phase 4 | T-4.1 解析器 / T-4.2 只读校验 / T-4.3 脱敏 / T-4.4 限制 / T-4.5 审计 | `security/` |
| Phase 5 | T-5.1 连接管理 / T-5.2 日志与刷新 | `cli/main.py` |
| Phase 6 | T-6.1 指标 / T-6.2 健康检查 | `observability/` |
| Phase 7 | T-7.1 客户端集成 / T-7.2 安全边界 / T-7.3 端到端 | `tests/` |

## 验收

- 单元 + 边界测试 163 个通过（含 SEC/MASK/LIMIT/TIMEOUT/CFG 清单）
- MCP stdio 客户端端到端：工具发现、资源模板、调用、错误结构
- 真实 PG/MySQL 集成测试由环境变量开关（`docker-compose.yml` 提供环境）

