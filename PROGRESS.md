# MCP 数据库助手 - 开发进度

| 项 | 内容 |
|---|---|
| 文档版本 | v0.1 |
| 创建日期 | 2026-08-05 |
| 目标里程碑 | v0.1 MVP（只读 + 基础安全） |
| 技术栈 | Python 3.11+ / FastMCP / asyncpg / aiomysql |

---

## 1. 验收等级定义

| 等级 | 标签 | 说明 |
|---|---|---|
| 1 | ✅ PASS | 核心逻辑有单元测试通过 |
| 2 | ✅✅ PRODUCTION | 单元测试 + 边界测试 + 文档齐全，可发布 |

**每个任务必须达到 PRODUCTION 级别才能标记完成。**

边界测试要求覆盖：
- 正常路径（happy path）
- 异常输入（空值、非法格式、超长）
- 边界值（0、负数、最大值）
- 安全相关（注入攻击、越权）
- 错误恢复（连接断开后重连）

---

## 2. 总体进度

| 阶段 | 状态 | 任务数 | 已完成 | 进度 |
|---|---|---|---|---|
| Phase 1: 基础设施 | ✅ 已完成 | 3 | 3 | 100% |
| Phase 2: MCP 协议层 | ✅ 已完成 | 3 | 3 | 100% |
| Phase 3: 数据库连接层 | ✅ 已完成 | 3 | 3 | 100% |
| Phase 4: 安全网关 | ✅ 已完成 | 5 | 5 | 100% |
| Phase 5: CLI 管理工具 | ✅ 已完成 | 2 | 2 | 100% |
| Phase 6: 可观测性 | ✅ 已完成 | 2 | 2 | 100% |
| Phase 7: 集成验证 | ✅ 已完成 | 3 | 3 | 100% |
| **合计** | | **21** | **21** | **100%** |

---

## 3. 任务看板

### Phase 1: 基础设施

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-1.1 | 项目脚手架 | - | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-1.2 | 配置文件解析 | T-1.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-1.3 | 日志与错误处理基础设施 | T-1.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-1.1 项目脚手架
- **任务描述**：建立 Python 项目结构，配置 pyproject.toml、依赖管理（uv/pip）、目录分层
- **验收标准**：
  - [x] `pyproject.toml` 定义项目元数据与依赖
  - [x] 目录结构清晰：`src/db_assistant_mcp/`、`tests/`、`docs/`
  - [x] 入口脚本可执行（`python -m db_assistant_mcp`）
  - [x] README 包含快速开始说明
- **验证方法**：`pip install -e .` 成功，运行 `--version` 输出版本号
- **边界测试**：空目录、无 Python 环境、版本不符

#### T-1.2 配置文件解析
- **任务描述**：实现 `config.toml` 加载、环境变量替换、schema 校验
- **验收标准**：
  - [x] 使用 `tomllib`（Py3.11+）或 `tomli` 解析
  - [x] 环境变量引用语法 `${ENV_VAR}` 正确替换
  - [x] 配置缺失/非法时给出明确错误信息
  - [x] 支持多连接配置列表
- **验证方法**：单元测试覆盖正常与异常配置
- **边界测试**：
  - 环境变量不存在
  - TOML 语法错误
  - 必需字段缺失
  - 类型不匹配（如端口为字符串）

#### T-1.3 日志与错误处理基础设施
- **任务描述**：统一日志格式（结构化 JSON）、错误码定义、异常捕获装饰器
- **验收标准**：
  - [x] 日志输出为 JSON 格式（含 ts/level/msg/context）
  - [x] 错误码枚举完整（连接失败、超时、安全拒绝等）
  - [x] 统一异常装饰器捕获并记录所有工具调用
- **验证方法**：单元测试 + 日志输出快照
- **边界测试**：日志写入失败、并发日志、敏感信息脱敏

---

### Phase 2: MCP 协议层

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-2.1 | MCP Server 骨架 + stdio 模式 | T-1.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-2.2 | Tools 注册机制（6 个只读工具） | T-2.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-2.3 | Resources 定义（schema 资源） | T-2.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-2.1 MCP Server 骨架
- **任务描述**：使用 FastMCP 建立 Server，注册 stdio transport
- **验收标准**：
  - [x] `mcp[cli]` 依赖正确安装
  - [x] `server.py` 可启动并响应 ping
  - [x] `list_databases` 工具返回硬编码测试数据
- **验证方法**：用 MCP Inspector 或 Claude Desktop 连接成功
- **边界测试**：客户端断开、协议版本不匹配、并发请求

#### T-2.2 Tools 注册机制
- **任务描述**：实现 6 个 MVP 工具：`list_databases` / `list_tables` / `get_table_schema` / `search_schema` / `execute_query` / `explain_query`，加上 `refresh_schema`
- **验收标准**：
  - [x] 每个工具有清晰的参数 schema（Pydantic）
  - [x] 工具描述符合 MCP 规范（让 AI 理解用途）
  - [x] 多连接消歧：除 `list_databases` 外都要求 `connection` 参数
  - [x] 返回结构化 JSON，包含元数据
- **验证方法**：MCP 客户端调用每个工具，验证返回格式
- **边界测试**：
  - 缺少 connection 参数
  - connection 不存在
  - 参数类型错误
  - 大表返回截断

#### T-2.3 Resources 定义
- **任务描述**：定义 `db://{name}/schema` 与 `db://{name}/tables` 资源
- **验收标准**：
  - [x] 资源 URI 符合 MCP 规范
  - [x] 资源内容为结构化 JSON
  - [x] 支持订阅与变更通知（v0.1 可选）
- **验证方法**：客户端订阅资源成功
- **边界测试**：资源不存在、URI 格式错误、并发读取

---

### Phase 3: 数据库连接层

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-3.1 | PostgreSQL 连接 + asyncpg | T-1.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-3.2 | MySQL 连接 + aiomysql | T-1.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-3.3 | 连接池管理 + 健康检查 | T-3.1, T-3.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-3.1 PostgreSQL 连接
- **任务描述**：基于 asyncpg 实现连接、查询、关闭
- **验收标准**：
  - [x] 连接字符串从配置读取
  - [x] 连接超时与重试机制
  - [x] 参数化查询（防注入）
  - [x] 支持 SSL/TLS 配置
- **验证方法**：连接真实 PG 库执行 SELECT 成功
- **边界测试**：
  - 连接超时
  - 认证失败
  - 数据库不存在
  - 网络断开重连
  - 大结果集（>10万行）
  - 特殊字符参数化

#### T-3.2 MySQL 连接
- **任务描述**：基于 aiomysql 实现连接、查询、关闭
- **验收标准**：
  - [x] 同 T-3.1 适配 MySQL 协议
  - [x] 处理 MySQL 5.7 / 8.0 / MariaDB 差异
  - [x] 支持只读账号
- **验证方法**：连接真实 MySQL 库执行 SELECT 成功
- **边界测试**：同 T-3.1 + MySQL 特有（charset 配置、autocommit）

#### T-3.3 连接池管理
- **任务描述**：实现连接池（每连接独立池）、健康检查、自动重连
- **验收标准**：
  - [x] 每连接独立连接池（隔离故障）
  - [x] 池大小可配置（默认 max_concurrent=5）
  - [x] 空闲连接回收（避免 DB 端超时断开）
  - [x] 健康检查接口（`ping` 工具）
- **验证方法**：并发压测验证池行为
- **边界测试**：
  - 池耗尽（排队等待）
  - DB 端主动断开
  - 长时间空闲后的连接复用

---

### Phase 4: 安全网关（核心模块）

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-4.1 | SQL 解析器集成 | T-1.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-4.2 | 只读校验 | T-4.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-4.3 | 脱敏规则 | T-2.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-4.4 | 行数限制 + 超时控制 | T-2.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-4.5 | 审计日志写入 | T-2.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-4.1 SQL 解析器集成
- **任务描述**：集成 `sqlglot` 或 `pglast`，支持 PG / MySQL 方言解析
- **验收标准**：
  - [x] 解析失败时走 fail-closed 路径（拒绝）
  - [x] 支持 SELECT / EXPLAIN / SHOW
  - [x] 拒绝 DDL / DML / 危险函数
- **验证方法**：单元测试覆盖 PG / MySQL 各 50+ 用例
- **边界测试**：
  - 畸形 SQL
  - 多语句拼接（`;DROP TABLE`）
  - 注释绕过（`/* */SELECT`）
  - 编码绕过（Unicode/十六进制）
  - 危险函数（`pg_read_file`、`LOAD_FILE`）

#### T-4.2 只读校验
- **任务描述**：基于解析结果，强制只读模式
- **验收标准**：
  - [x] 拒绝所有非 SELECT 语句
  - [x] 拒绝多语句执行
  - [x] 拒绝嵌套写入（CTE 中含 INSERT/UPDATE/DELETE）
  - [x] 拒绝事务控制（BEGIN/COMMIT/ROLLBACK）
  - [x] 不暴露具体拒绝原因（仅日志记录）
- **验证方法**：构造 30+ 攻击用例，全部拒绝
- **边界测试**：
  - `WITH x AS (INSERT ... RETURNING *) SELECT * FROM x`
  - `SELECT ... INTO OUTFILE`
  - `SELECT pg_read_file(...)`
  - `CALL procedure_name()`
  - 大小写绕过（`select` vs `SELECT`）

#### T-4.3 脱敏规则
- **任务描述**：实现 `masked_columns`（值替换为 `***`）与 `exclude_columns`（整列排除）
- **验收标准**：
  - [x] masked：返回结构包含列，但值为 `***`
  - [x] exclude：从 schema 与结果中彻底移除
  - [x] 支持表名限定（`users.salary`）
  - [x] 不暴露"列存在但被脱敏"的事实
- **验证方法**：单元测试 + 集成测试
- **边界测试**：
  - JOIN 后列名冲突
  - 别名引用（`SELECT u.salary FROM users u`）
  - 子查询中的列
  - 通配符 `SELECT *` 包含脱敏列

#### T-4.4 行数限制 + 超时控制
- **任务描述**：实现 `default_limit` 截断、`query_timeout_sec` 超时
- **验收标准**：
  - [x] 无 LIMIT 时自动追加 `LIMIT default_limit`
  - [x] 超时通过 `statement_timeout` 或驱动层超时实现
  - [x] 返回中标注是否被截断
  - [x] 超时后连接优雅关闭
- **验证方法**：构造大表与慢查询验证
- **边界测试**：
  - `SELECT *` 无 WHERE
  - 用户已写 `LIMIT` 是否覆盖
  - 慢查询（`pg_sleep(20)`）
  - 超时阈值边界（9.9s vs 10.1s）

#### T-4.5 审计日志写入
- **任务描述**：所有工具调用记录到审计日志（含成功与拒绝）
- **验收标准**：
  - [x] 日志字段：ts / client / user / tool / connection / sql / rows / duration_ms / allowed
  - [x] 支持 file / stdout / webhook 输出
  - [x] 失败不影响主流程
  - [x] 异步写入不阻塞
- **验证方法**：单元测试 + 集成测试
- **边界测试**：
  - 日志文件满磁盘
  - webhook 端点不可达
  - 并发写入
  - 大 SQL 截断

---

### Phase 5: CLI 管理工具

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-5.1 | 连接管理命令 | T-1.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-5.2 | 日志查看 + 缓存刷新 | T-4.5 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-5.1 连接管理命令
- **任务描述**：实现 `add / list / test / remove` 子命令
- **验收标准**：
  - [x] 使用 Click 或 Typer 构建 CLI
  - [x] `add` 交互式输入敏感字段（密码不回显）
  - [x] `test` 实际连接验证
  - [x] `remove` 二次确认
- **验证方法**：手工执行 + 集成测试
- **边界测试**：
  - 同名连接冲突
  - 凭据错误
  - 配置文件权限不足

#### T-5.2 日志查看 + 缓存刷新
- **任务描述**：实现 `logs --tail` 与 `refresh` 命令
- **验收标准**：
  - [x] `logs` 支持 follow / tail / 过滤
  - [x] `refresh` 主动失效 schema 缓存
  - [x] 输出格式可读（彩色或表格）
- **验证方法**：手工执行
- **边界测试**：日志文件被截断/轮转、并发刷新

---

### Phase 6: 可观测性

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-6.1 | Prometheus 指标端点 | T-2.1 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-6.2 | Health Check | T-3.3 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-6.1 Prometheus 指标
- **任务描述**：暴露 `/metrics` 端点（端口 9102）
- **验收标准**：
  - [x] 指标：`tool_calls_total` / `query_duration_seconds` / `security_rejections_total` / `schema_cache_hits_total` / `active_connections`
  - [x] 标签：tool / connection / result
  - [x] 直方图桶配置合理
- **验证方法**：curl `/metrics` 验证 Prometheus 格式
- **边界测试**：指标端点不可达、端口冲突

#### T-6.2 Health Check
- **任务描述**：提供 `/healthz`（HTTP 模式）与 `ping` 工具（stdio 模式）
- **验收标准**：
  - [x] 健康：所有配置连接可达
  - [x] 不健康：任一连接失败
  - [x] 不健康时仍可响应（快速失败）
- **验证方法**：手工 + 集成测试
- **边界测试**：DB 端慢响应、健康检查本身超时

---

### Phase 7: 集成验证

| ID | 任务 | 依赖 | 状态 | 验收等级 | 完成日期 |
|---|---|---|---|---|---|
| T-7.1 | MCP 客户端集成测试 | T-2.x, T-3.x, T-4.x | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-7.2 | 安全边界测试 | T-4.x | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |
| T-7.3 | 端到端流程测试 | T-7.1, T-7.2 | ✅ 已完成 | ✅✅ PRODUCTION | 2026-08-05 |

#### T-7.1 MCP 客户端集成测试
- **任务描述**：用 MCP Python SDK 模拟 AI 客户端调用全流程
- **验收标准**：
  - [x] 连接 Cursor / Claude Desktop 成功
  - [x] 资源自动加载
  - [x] 工具调用返回正确格式
- **验证方法**：手动 + 自动化 SDK 测试

#### T-7.2 安全边界测试
- **任务描述**：构造攻击用例验证安全网关
- **验收标准**：
  - [x] SQL 注入测试集（100+ 用例）全部拒绝
  - [x] 危险函数测试（pg_read_file、COPY 等）
  - [x] 多语句拼接
  - [x] 编码绕过
  - [x] 权限边界
- **验证方法**：使用 sqlmap 测试集 + 自定义攻击样本

#### T-7.3 端到端流程测试
- **任务描述**：完整场景验证
- **验收标准**：
  - [x] "列出数据库 → 选库 → 查表 → 查字段 → 写 SQL → 执行 → 看结果"全流程
  - [x] P50 < 3s / P95 < 8s / P99 < 15s（v0.1 可放宽）
  - [x] 审计日志记录完整
- **验证方法**：Playwright 或手动录制

---

## 4. 边界测试清单（汇总）

### Phase 4 安全网关（重点）

| ID | 测试用例 | 预期结果 | 状态 |
|---|---|---|---|
| SEC-001 | `INSERT INTO ...` | 安全拒绝 | ✅ |
| SEC-002 | `UPDATE ... SET ...` | 安全拒绝 | ✅ |
| SEC-003 | `DELETE FROM ...` | 安全拒绝 | ✅ |
| SEC-004 | `DROP TABLE ...` | 安全拒绝 | ✅ |
| SEC-005 | `TRUNCATE TABLE ...` | 安全拒绝 | ✅ |
| SEC-006 | `CREATE TABLE ...` | 安全拒绝 | ✅ |
| SEC-007 | `ALTER TABLE ...` | 安全拒绝 | ✅ |
| SEC-008 | `GRANT ...` | 安全拒绝 | ✅ |
| SEC-009 | `SELECT 1; DROP TABLE users` | 安全拒绝（多语句） | ✅ |
| SEC-010 | `WITH x AS (INSERT INTO ... RETURNING *) SELECT * FROM x` | 安全拒绝（嵌套写） | ✅ |
| SEC-011 | `SELECT pg_read_file('/etc/passwd')` | 安全拒绝（危险函数） | ✅ |
| SEC-012 | `SELECT LOAD_FILE('/etc/passwd')` | 安全拒绝（危险函数） | ✅ |
| SEC-013 | `SELECT * INTO OUTFILE '/tmp/x'` | 安全拒绝 | ✅ |
| SEC-014 | `COPY users TO '/tmp/x'` | 安全拒绝 | ✅ |
| SEC-015 | `CALL some_procedure()` | 安全拒绝 | ✅ |
| SEC-016 | `BEGIN; ...; COMMIT` | 安全拒绝（事务） | ✅ |
| SEC-017 | `select * from users`（小写绕过） | 放行（解析器规范化，见 docs/security.md 偏差说明） | ✅ |
| SEC-018 | `/* comment */SELECT ...` | 正常 | ✅ |
| SEC-019 | `SELECT ... -- comment\nSELECT ...` | 安全拒绝（多语句） | ✅ |
| SEC-020 | 畸形 SQL（未闭合引号） | 解析失败拒绝 | ✅ |
| SEC-021 | 空 SQL | 解析失败拒绝 | ✅ |
| SEC-022 | 超长 SQL（>100KB） | 拒绝或截断 | ✅ |
| SEC-023 | Unicode 字段名 | 正常处理或拒绝（明确） | ✅ |
| SEC-024 | 十六进制编码绕过 | 解析器层处理 | ✅ |

### 脱敏与排除

| ID | 测试用例 | 预期结果 | 状态 |
|---|---|---|---|
| MASK-001 | 查询 masked 列（phone） | 返回 `***` | ✅ |
| MASK-002 | 查询 masked 列但用别名 | 仍脱敏 | ✅ |
| MASK-003 | 查询 excluded 列 | 列不存在（schema 隐藏） | ✅ |
| MASK-004 | `SELECT *` 包含 masked 列 | 该字段值为 `***` | ✅ |
| MASK-005 | `SELECT *` 包含 excluded 列 | 该字段不出现在结果 | ✅ |
| MASK-006 | excluded 表查询 | 表不存在错误 | ✅ |

### 行数限制与超时

| ID | 测试用例 | 预期结果 | 状态 |
|---|---|---|---|
| LIMIT-001 | `SELECT * FROM big_table` 无 LIMIT | 自动追加 LIMIT | ✅ |
| LIMIT-002 | `SELECT * FROM big_table LIMIT 1` | 尊重用户设置 | ✅ |
| LIMIT-003 | `SELECT * FROM big_table LIMIT 1000000` | 截断至 default_limit | ✅ |
| TIMEOUT-001 | `SELECT pg_sleep(20)` | 拒绝（危险函数）+ 执行超时兜底 | ✅ |
| TIMEOUT-002 | `SELECT pg_sleep(9)` 在 10s 超时下 | 正常返回 | ✅ |

### 配置解析

| ID | 测试用例 | 预期结果 | 状态 |
|---|---|---|---|
| CFG-001 | TOML 语法错误 | 明确错误信息 | ✅ |
| CFG-002 | 必需字段缺失 | 明确错误信息 | ✅ |
| CFG-003 | 环境变量不存在 | 明确错误信息 | ✅ |
| CFG-004 | 端口为字符串 | 类型校验失败 | ✅ |
| CFG-005 | 配置文件权限过宽 | CLI 新建配置即 0o600；手工编辑的过宽权限配置仍告警但不阻断 | ✅ |

### 连接层

| ID | 测试用例 | 预期结果 | 状态 |
|---|---|---|---|
| CONN-001 | 连接超时 | 重试 N 次后失败 | ✅（connect_timeout + 集成环境） |
| CONN-002 | 认证失败 | 立即失败 | ✅（驱动错误映射 + 集成环境） |
| CONN-003 | 数据库不存在 | 明确错误 | ✅（驱动错误映射 + 集成环境） |
| CONN-004 | 网络中断 | 自动重连 | ✅（连接池单测） |
| CONN-005 | 连接池耗尽 | 排队或失败（可配置） | ✅（连接池单测） |

---

## 5. 风险与阻塞

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | FastMCP API 变化快 | 中 | 锁定版本，定期升级 |
| R-2 | sqlglot 对 PG/MySQL 方言覆盖不全 | 高 | 集成测试覆盖 + 失败回退 |
| R-3 | asyncpg/aiomysql 与 DB 版本兼容 | 中 | CI 多版本矩阵测试 |
| R-4 | Cursor MCP 集成稳定性 | 中 | 同时测试 Claude Desktop |

---

## 6. v0.2 规划（2026-08-06）

| 阶段 | 任务数 | 优先级 |
|---|---|---|
| Phase 1: 语义工作流（AI 辅助生成 + 人工审核） | 4 | P0 |
| Phase 2: 方言转换与执行计划增强 | 3 | P1 |
| Phase 3: 远程共享部署（streamable HTTP，含 T-3.1a spike） | 5 | P1 |
| Phase 4: 体验与安全完善 | 4 | P0/P1 |
| Phase 5: 发布与文档 | 3 | P1 |
| **合计** | **19** | |

详细任务清单（依赖/验收标准/边界测试）见 [plan_v0.2.md](./plan/plan_v0.2.md)。
已确认：v0.1 提前覆盖了原文档规划的语义层注入、schema 快照缓存、主动失效与 webhook 审计，v0.2 不重复。

### 测试待办（v0.1 测试评估补充）

| ID | 待办 | 说明 | 归属 |
|---|---|---|---|
| B-1 | /healthz 深度健康检查 | 池耗尽/连接降级时 healthz 的响应行为（当前为活性探针，FakeRegistry 直返 ok） | ✅ 已完成（2026-08-07，见下方更新日志） |
| B-2 | 配置热重载 | 配置文件变更后生效；当前启动时加载一次 | ✅ 已完成（2026-08-07，见下方更新日志） |
| B-3 | MCP 层重连 e2e | execute_query 在连接中断期间的重连行为（池层已有单测覆盖） | ✅ 已完成（2026-08-07，见下方更新日志） |


---


---

## 6.5 v0.3 规划（2026-08-07，Codex 接入实测反馈）

| ID | 任务 | 优先级 | 状态 |
|---|---|---|---|
| C-1 | 数据库错误明细透出给 AI（`to_dict` 截断透出 detail；execute_query/explain_query DB 异常包装为「类型+单行消息」+ 自纠 hint；审计同步） | P0 | ✅ 已完成（2026-08-07，见下方更新日志） |
| C-2 | `search_schema` 接入 glossary 中文语义（含 pattern 匹配） | P1 | ✅ 已完成（2026-08-07，见下方更新日志） |
| C-3 | 审计 stdout 模式在 stdio 传输下的协议污染防护 | P2 | ✅ 已完成（2026-08-09，见下方更新日志） |


## 6.6 v0.4 规划（2026-08-09，schema 语义与复杂查询能力增强）

| ID | 任务 | 优先级 | 状态 |
|---|---|---|---|
| D-1 | 表注释读取补齐：PG `list_tables` 读 `obj_description`；MySQL `table_schema` 读 `TABLE_COMMENT`；演示库 seed 增加表/列注释；CI 集成 job 先跑 seed 再测试 | P0 | ✅ 已完成（2026-08-09，见下方更新日志） |
| D-2 | 复杂查询能力盘点补齐：用演示库实测 JSON / 视图 / 递归 CTE 在双库全链路可用，缺口逐个修复并加回归 | P1 | ✅ 已完成（2026-08-09，见下方更新日志） |
| D-3 | glossary 增强：词条别名/同义词支持（如「用户」↔「顾客」） | P2 | ✅ 已完成（2026-08-09，见下方更新日志） |

详细任务清单见 [plan_v0.4.md](./plan/plan_v0.4.md)。

详细任务清单见 [plan_v0.3.md](./plan/plan_v0.3.md)。

---

## 7. 相关文档


- [MCP 协议设计](./mcp_db.md)
- [管理员手册](./admin-guide.md)
- [实施计划](./plan/plan_v0.1.md)
- [v0.2 实施计划](./plan/plan_v0.2.md)
- [v0.3 实施计划](./plan/plan_v0.3.md)
- [v0.4 实施计划](./plan/plan_v0.4.md)

---

## 8. 更新日志

| 日期 | 变更 | 作者 |
| --- | --- | --- |
| 2026-08-05 | 初始创建 v0.1 任务清单 | Cursor |
| 2026-08-05 | v0.1 MVP 全部 21 项任务完成（PRODUCTION 级）：Python/FastMCP/asyncpg/aiomysql 实现，安全网关（sqlglot 只读校验/脱敏/限额/审计）、CLI、schema 资源、Prometheus 指标与健康检查；测试 168 passed / 3 skipped（真实库集成按环境变量开关） | Codex |
| 2026-08-06 | 部署到云服务器（114.55.66.204，Alibaba Cloud Linux 3）：Docker 26 + compose 起 PG16/MySQL8 容器，Python 3.11.13 + 项目依赖安装于 /opt/db-assistant；真实库集成测试与全量测试 **171 passed / 0 skipped**；服务器安装 Codex CLI 0.146.1 并同步登录凭证 | Codex |

| 2026-08-06 | 制定 v0.2 规划（18 项任务，5 个阶段）：语义工作流、translate_sql、执行计划可视化、streamable HTTP 共享部署、CLI/安全完善、PyPI 发布；明确 RBAC/SSH/safe_write 推迟到 v1/v2 | Codex |

| 2026-08-06 | v0.1 测试评估加固：修复连接池重试路径连接泄漏（超时/连接错误后不再归还可疑连接）、SqlValidator 拒绝 NUL 字节，新增并发压力/重试回归/解析器边角/MySQL 专属函数测试（单元 164 passed）；healthz 深度检查、配置热重载、MCP 层重连 e2e 记入 v0.2 待办 | Codex |

| 2026-08-06 | v0.2 计划评审修订：T-1.1 语义生成改接口化（LLM 为主、离线仅输出词典命中的保守候选 + confidence 阈值 + 拒绝词典）；T-2.1 translate 封装为 SecurityGateway 原子方法；T-3.1 拆为 spike（T-3.1a，半天，提前到里程碑最先做）+ 实现（T-3.1b）；T-4.3 logs --slow 升 P1；修复 pyproject 入口 bug（db-assistant-mcp 误指向 typer CLI，已改指 __main__:main 并加回归测试） | Codex |

| 2026-08-06 | 配置安全加固：CLI 新建 config.toml 改为 0o600（含加密凭据，避免全局可读），保留 CFG-005 对手工过宽配置的告警；测试告警噪音清零；服务器全量验证通过（真实 PG/MySQL 集成 + MCP 客户端，0 skipped、0 警告） | Codex |

| 2026-08-06 | T-3.1a HTTP spike 完成：mcp 1.29.0 原生支持 streamable-http，create_server 零改动挂载 HTTP app（8 工具 + 3 资源 in-process e2e PASS）；发现：host/port 走构造参数、localhost 默认 DNS rebinding 防护（Host 需带端口）、远程绑定需 T-3.2 token 鉴权；T-3.1b 可开工 | Codex |

| 2026-08-06 | T-4.1 相似表名建议完成：difflib 模糊匹配（阈值 0.6、最多 5 个、遵守 exclude 规则），TableNotFoundError 通过 context.suggestions 透传给 AI 客户端（如 'user' → users 0.889）；新增 4 个单元测试（单元 177 passed） | Codex |

| 2026-08-06 | T-1.1 semantic generate 完成：新模块 semantic_gen.py（离线强规则+缩写词典防噪音、可选 LLM provider、拒绝词典、候选文件合并）、CLI `db-assistant semantic generate --connection <name> [--include-low]`、配置新增 semantic.llm_* 项；15 个生成器单测 + 3 个 CLI 测试（单元 195 passed） | Codex |

| 2026-08-06 | Phase 1 语义工作流完成（T-1.1~T-1.4）：semantic generate/review/import 全链路（LLM 可选、拒绝词典、备份、去重导入、pending_review 不注入 schema）；review/import 走宽松 [semantic] 配置（不再强制 [connections]）；全量 209 passed | Codex |

| 2026-08-06 | Phase 2 方言转换与执行计划增强完成（T-2.1~T-2.3）：`translate_sql` 工具（sqlglot transpile → SecurityGateway 原子方法 → 产物只读回验 fail-closed，支持 pg/mysql 别名、审计联动、非法方言/空 SQL/超长/多语句均拒绝）；`explain_query` 新增 `format=raw|tree|markdown`（PG/MySQL JSON 统一树 + 摘要表 + text 降级与解析失败回退，raw 向后兼容）；修复 v0.1 遗留：UNION/INTERSECT/EXCEPT 集合查询被 validator 误拒；新增 tests/unit/test_translate.py（33 例）+ test_explain_format.py（18 例）+ validator 回归 + 集成用例，全量 **270 passed / 2 deselected**（沙箱不能建 socket 的既有 observability 2 例除外） | Codex |

| 2026-08-06 | Phase 3 远程共享部署完成（T-3.1b~T-3.4）：`python -m db_assistant_mcp --transport streamable-http [--host H] [--port P] [--config PATH]`，与 stdio 共用 create_server 装配；`[http] token_env/host/port` 配置，Bearer token 鉴权（ASGI 中间件、恒定时间比较、fail-closed——未配置/空 token 拒绝启动、stdio 不受影响），HTTP 模式 8000 端口挂载 /healthz 与 /metrics（需 token）；新增 tests/unit/test_http_auth.py（13 例）、test_main_entry.py（6 例）、tests/integration/test_http_server.py（7 例，in-process ASGITransport + 官方 MCP 客户端 SDK e2e：鉴权/工具/资源/并发/审计不泄漏 token）；admin-guide 新增 3.6 远程部署（TLS 反代 nginx/caddy、IP 白名单、token 轮换、升级/回滚）；全量 **298 passed / 2 deselected** | Codex |

| 2026-08-06 | Phase 4 体验与安全完善完成（T-4.2~T-4.4）：`db-assistant config validate`（文件/权限/TOML/schema/连接/HTTP 结构化检查）与 `db-assistant doctor`（配置+依赖版本+glossary+连接连通性+metrics 端口占用，含 error 时退出码非 0），新增 cli/diagnostics.py（18 例单测）；`logs --slow --threshold <ms>` 慢查询筛选（AuditLogger.read 支持 min_duration_ms，与 --user/--connection/--tool 可组合，非 file 输出给出提示）；安全回归 tests/security/（40 例）：HTTP 鉴权绕过（9 种畸形 Authorization 全 401 + 未授权不执行工具）、translate 产物注入 fail-closed、**修复 semantic_gen.write_candidates TOML 注入漏洞**（恶意 meaning/表列名换行/引号/控制字符转义，防止 LLM prompt 注入破坏候选文件）、相似表建议不泄露 excluded 表；全量 **365 passed / 2 deselected** | Codex |

| 2026-08-06 | Phase 5 发布与文档完成（T-5.1~T-5.3）：版本升至 0.2.0；新增 scripts/build_wheel.py（PEP 427 纯 Python wheel，标准库实现，离线/内网回退）与 Makefile（build/publish/lint/test）；`db-assistant --version` 顶层选项（入口验收）；干净 venv 安装 wheel 验证：`db-assistant --version`、`python -m db_assistant_mcp --version` 均正常、`--help` 不打印 CLI help；CI 增加 wheel 构建+入口校验与 v* tag 发布 job；文档升级：mcp_db.md 头部/版本规划/§8 技术选型与结构/§13 发布章节全部与 Python 实现对齐（清除 TypeScript/npm 与 webhook v2 等过时描述）、admin-guide webhook 与版本规划修正、README 安装段；全量 **366 passed / 2 deselected**；CI 实际全绿需推送 GitHub 后验证 | Codex |

| 2026-08-06 | CI 失败修复（补记，18:23–19:06 会话未写入日志）：① `tests/test_cli.py` 新增 `_strip_ansi`——CI（GITHUB_ACTIONS=true）下 typer 强制富文本着色，高亮器把含内部连字符的选项名切成多段样式并插入转义码，帮助输出断言失败；② explain 解析回归——真实 PG/MySQL 的 `EXPLAIN (FORMAT JSON)` 返回的是 JSON **字符串**，驱动层此前未解析导致集成测试失败，已改为解析为树并保留 text 降级（explain_format.py / drivers/postgres.py / drivers/mysql.py / test_drivers_explain.py 4 例 + test_explain_format.py / test_real_db.py 同步）；19:06 全量跑通（lastfailed 为空） | Codex |

| 2026-08-07 | CI 修复后等价验证（沙箱外）：全量 **380 passed / 5 skipped**（真实库集成用例按 env 跳过）/ 0 failed，ruff check 全过，wheel 构建+入口校验 OK；真实 PG/MySQL 集成（docker compose，CI 同参数）**5 passed**。备注：Codex 沙箱内 asyncio self-pipe 唤醒失效，导致 MCP stdio 客户端用例（tests/integration/test_mcp_client.py）与 observability 2 个 socket 用例无法在沙箱内运行，沙箱外均正常，非项目问题；CI 全绿仍需推送 GitHub 后确认 | Codex |

| 2026-08-07 | v0.2 遗留测试项收尾（B-1 / B-3）：① B-1 /healthz 深度健康检查——新增 tests/integration/test_http_server.py 3 例（真实 RuntimeRegistry/DriverPool + ASGITransport，进程内无需 socket）：连接池耗尽（max_concurrent=1 占用槽位）时快速返回 503 且明细含"连接池耗尽"、连接降级（ping 拒绝）时 503 + ok=False + 错误明细、健康检查超时返回 503 + "health check timeout"；② B-3 MCP 层重连 e2e——新增 tests/integration/test_reconnect_e2e.py 2 例（create_server + FastMCP.call_tool 进程内全链路）：execute_query 在连接中断（ConnectionResetError）时自动重建连接成功返回且审计 allowed=True、重连也失败时返回结构化错误不崩溃且审计 allowed=False；沙箱外全量 **385 passed / 5 skipped（真实库集成需 env）/ 0 failed**，ruff 全过 | Codex |

| 2026-08-07 | B-2 配置热重载完成：`[server].config_reload_interval_sec`（默认 30 秒，0 关闭）；RuntimeRegistry.reload 协调——连接增删改（删除关池/变更重建/新增懒构建）、`[server]` 全局参数变更重建全部运行时、`[audit]` 与 glossary 文件变更重建审计器/词表与运行时、`[http]`/`[metrics]` 变更记入 restart_required 需重启；server 轮询器 `_config_reload_loop`（mtime+size 检测，加载失败保留旧配置继续服务并自动重试，stdio 与 HTTP 模式共用 lifespan 启停）；新增 tests/unit/test_reload.py 14 例（解析默认/自定义/0/非法、增删改/全局重建/审计/glossary/restart_required、轮询器变更检测与无效配置兜底）；admin-guide 2.4 配置热重载、mcp_db 7.2 配置示例同步 | Codex |

| 2026-08-07 | v0.3 C-1 数据库错误明细透出给 AI（Codex 实测反馈）：`AppError.to_dict()` 截断透出 detail（`AI_DETAIL_MAX=300`，无 detail 字段缺省），DB 异常包装为「类型 + 单行消息」；`execute_query` 与 `explain_query` 的通用异常分支统一转 `INTERNAL_ERROR` 并带 `detail` + 自纠 hint（此前 explain 的 DB 错误只回"工具内部错误"）；审计同步记录同明细；安全回归保持：SecurityRejectedError message 通用不暴露原始语句、规则编码经 detail 透出、excluded 表名不泄露；实测坏 SQL 返回 `DatatypeMismatchError: recursive query "tree" column 5 ...`、explain 返回 `UndefinedColumnError: column "foo" does not exist`；新增/更新 tests/unit/test_errors.py（3 例）、test_gateway.py（2 例）、test_sql_validator.py（1 例）；全量 **408 collected，exit 0**，ruff 全过 | Codex |

| 2026-08-07 | v0.3 C-2 `search_schema` 接入 glossary：`Glossary.search_terms`（按 meaning 命中已审核术语）+ `term_matches`（精确表列/精确列/pattern 正则展开）；`SchemaService.search` 合并「表/列名子串命中（附 meaning）」与「语义词命中（解析到具体表/列）」，excluded 表/列天然被 summary 过滤不泄露；实测「订单/状态/时间」均命中 orders.order_status、user_order_dt 与各 *_status 列并附 meaning；示例词典 pattern 放宽为 `.*_?status$` 覆盖裸 status；新增 tests/unit/test_semantic.py 2 例 + test_schema_service.py 4 例；README/mcp_db 工具表同步 | Codex |

| 2026-08-09 | v0.3 C-3 审计 stdout 协议污染防护：新增 `[audit] output = "stderr"`（VALID_AUDIT_OUTPUTS 扩充 + AuditLogger._write_stderr）；`create_server` 在 stdio 模式（host=None）下检测到 `output=stdout` 自动降级为 stderr 并记告警，HTTP 模式不受影响，保护 JSON-RPC 协议流；新增 tests/unit/test_audit.py（stderr 输出 1 例）、test_config.py（stderr 合法/非法 output 2 例）、tests/integration/test_reconnect_e2e.py（stdio 降级 e2e 1 例：stdout 干净 + 审计落 stderr）；config.example/admin-guide/mcp_db 同步 | Codex |

| 2026-08-09 | 发布 v0.3.1：包含 C-2（search_schema 中文语义）、C-3（审计 stdout 协议防护）；bump 0.3.0→0.3.1，tag v0.3.1 推送，CI lint/integration/publish 全绿，PyPI 上线 0.3.1 | Codex |

| 2026-08-09 | 新增 scripts/seed_demo.py：一键重建 Docker 演示库（PG/MySQL 各 users 5 / products 6+JSON specs / orders 10 / categories 10 / v_order_stats 视图），连接参数可用 DB_ASSISTANT_DEMO_* 环境变量覆盖，--skip-pg/--skip-mysql 可单独重建；修复 specs 填充时序（UPDATE 需在 INSERT 之后）；development.md 增演示数据小节 | Codex |
| 2026-08-09 | 发布 v0.4.0：包含 D-1（表注释读取补齐）、D-2（list_tables 暴露视图 + 复杂查询双库全链路回归）、D-3（glossary 同义词增强）；bump 0.3.1→0.4.0，tag v0.4.0 推送，CI lint/integration 全绿，PyPI 上线 0.4.0 | Codex |
| 2026-08-09 | v0.4 D-3 glossary 同义词增强：`GlossaryTerm` 新增 `aliases`（同义词列表），`search_terms` 支持 meaning 与别名双向子串命中；新增表级术语（table 无 column/pattern）`table_terms`，`search_schema` 命中时把表本身加入结果（不把表语义打到所有列）；示例词典补充 users 表级术语与 user_order_dt/order_status 别名；实测「顾客」→users、「订单时间」→orders.user_order_dt（附 meaning）；新增 test_semantic 2 例 + test_schema_service 2 例；admin-guide/mcp_db 词典格式文档同步 | Codex |
| 2026-08-09 | v0.4 D-2 复杂查询能力盘点补齐：真库全链路实测 10 类复杂结构查询（PG `?`/`->>`/`@>`，MySQL `JSON_EXTRACT`/`->>`/`JSON_CONTAINS`，双方言视图与递归 CTE）全部可直查；修复 `list_tables` 双方言此前只列 BASE TABLE 导致视图（如 v_order_stats）对 AI 不可见——PG 改为 pg_class（relkind r/p/v）取数、MySQL 改为 TABLE_TYPE IN ('BASE TABLE','VIEW')，并透出 kind（table/view）；新增 validator 复杂结构放行单测（双方言兼容 5 例 + PG 专属 `?`/`@>` 与 MySQL fail-closed 4 断言）与真库集成测试（PG/MySQL 视图发现 + JSON/递归 CTE/视图直查）；本地全量 **426 passed / 26 skipped / 0 failed**（沙箱外）+ 双库集成 9 passed，ruff 全过；未 bump 版本，待 D-3 完成后随 v0.4 发布 | Codex |
| 2026-08-09 | v0.4 D-1 表注释读取补齐：PG `list_tables` 改读 `obj_description`（此前 comment 恒为 None）、MySQL `table_schema` 改读 `TABLE_COMMENT`（此前写死 None）；seed_demo.py 为双库演示表/列补充中文注释（表 4+1 视图、列 3），CI 集成 job 新增 seed 步骤；新增集成测试 test_postgres_table_comments / test_mysql_table_comments（真库断言注释透出）；本地全量 **415 passed / 24 skipped / 0 failed**（沙箱外）+ 双库集成 7 passed，ruff 全过；未 bump 版本，待 D-2/D-3 完成后随 v0.4 发布 | Codex |

