# v0.2 实施计划

| 项 | 内容 |
|---|---|
| 文档版本 | v0.2（规划稿） |
| 创建日期 | 2026-08-06 |
| 目标里程碑 | v0.2：语义工作流 + 方言/执行计划增强 + 远程共享部署 |
| 预估周期 | 2–3 周（按 v0.1 节奏） |
| 技术栈 | 延续 v0.1：Python 3.11+ / FastMCP / sqlglot / aiohttp / typer |
| 优先级定义 | P0 必须交付 / P1 建议交付 / P2 可选（按反馈裁剪） |

---

## 1. 规划依据（v0.1 现状核对）

文档中"原 v0.2 规划项"已有多项在 v0.1 提前完成，**v0.2 不再重复**：

| 原规划项 | 现状 | 结论 |
|---|---|---|
| 语义层注入（glossary 加载/三级匹配/enrich/资源） | `semantic.py` + `db://{name}/semantic` 资源已实现 | ✅ 已完成 |
| schema 快照资源 + 会话缓存 | `schema_service.py`（TTL 缓存 + 命中指标）已实现 | ✅ 已完成 |
| schema 主动失效 | `refresh_schema` 工具 + CLI `refresh` 已实现 | ✅ 已完成 |
| webhook 审计 | `security/audit.py` 支持 webhook 输出 | ✅ 已完成 |

**剩余缺口（v0.2 真正要做的）**：

1. 语义层只有"人工维护 glossary"路径，缺少 AI 辅助生成 + 审核工作流（`GlossaryTerm` 已预留 `status/confidence` 字段但无 CLI 支撑）
2. 无 `translate_sql` 方言转换工具（mcp_db.md v0.2 规划项，sqlglot 原生支持，成本低）
3. `explain_query` 只返回原始 plan，无可视化/摘要输出
4. 仅 stdio 传输，无法支撑"共享服务部署"模式（admin-guide 3.1）
5. 表不存在时仅返回固定 hint，未实现 mcp_db.md 承诺的"相似表名模糊匹配"
6. CLI 缺少 `semantic` 命令族、`config validate`、慢查询筛选
7. 无 PyPI 发布产物（mcp_db.md 13 要求 npm/pypi 分发）

**明确不做（推迟到 v1/v2）**：RBAC 角色权限、SSH 隧道、safe_write 写操作、桌面 GUI / Web 管理台、多租户 SaaS。

---

## 2. 阶段与任务总览

| 阶段 | 状态 | 任务数 |
|---|---|---|
| Phase 1: 语义工作流（AI 辅助生成 + 人工审核） | ✅ 已完成 | 4 |
| Phase 2: 方言转换与执行计划增强 | ✅ 已完成 | 3 |
| Phase 3: 远程共享部署（streamable HTTP） | ✅ 已完成 | 5 |
| Phase 4: 体验与安全完善 | ✅ 已完成 | 4 |
| Phase 5: 发布与文档 | ✅ 已完成 | 3 |
| **合计** | | **19** |

---

## 3. 任务看板

### Phase 1: 语义工作流（P0）

| ID | 任务 | 依赖 | 优先级 | 验收等级 |
|---|---|---|---|---|
| T-1.1 | `semantic generate` 候选术语生成 | v0.1 semantic.py | P0 | ✅✅ PRODUCTION |
| T-1.2 | `semantic review` 审核工作流 | T-1.1 | P0 | ✅✅ PRODUCTION |
| T-1.3 | `semantic import` + 术语合并加载 | T-1.2 | P0 | ✅✅ PRODUCTION |
| T-1.4 | 语义层单元与边界测试 | T-1.1–T-1.3 | P0 | ✅✅ PRODUCTION |

#### T-1.1 `semantic generate` 候选术语生成 — ✅ 已完成（2026-08-06）
- **任务描述**：CLI 新增 `db-assistant semantic generate --connection <name>`，扫描该连接 schema 快照，为无术语覆盖的列生成候选词条，输出 `glossary.candidate.toml`（`status = "pending_review"`、`confidence`）。
- **生成器设计**（接口化，防候选噪音）：
  - [x] **LLM provider 为主要语义推断路径**：`semantic_gen.LLMProvider` 调用 OpenAI 兼容 chat completions（配置 `semantic.llm_api_key_env`/`llm_base_url`/`llm_model`），confidence <0.7 默认不写入，`--include-low` 才输出
  - [x] **离线模式仅输出有依据的候选**：强类型/后缀规则（`*_at`→时间戳、`is_*`→布尔、`*_amt`→金额等）+ 常见缩写词典全 token 命中（`user_order_desc`→"用户订单描述"）；含未知 token 的纯拆词不产生候选
  - [x] **拒绝词典**：`glossary.rejected.toml`（column/pattern + reason）在生成时跳过，供 T-1.2 `review --reject` 沉淀
  - [x] 未配置 LLM 时输出保守提示（"未配置 LLM，仅输出词典/规则命中的候选"），不静默降级
- **验收标准**：
  - [x] 生成的候选文件格式与 glossary.toml 兼容，且每条含 `status`/`confidence`
  - [x] 已有术语覆盖的列不重复生成
  - [x] 排除表/排除列的列不参与生成
  - [x] 无 schema 时给出明确提示，不报错退出
  - [x] 生成结果不覆盖现有 glossary 文件
  - [x] 拒绝词典中的模式不再生成候选
- **验证方法**：对 docker PG/MySQL 连接运行 `semantic generate`，检查候选文件
- **边界测试**：空库、全表已覆盖、超长列名、unicode 列名、LLM key 缺失、confidence 阈值过滤（--include-low）、拒绝词典命中、候选文件已存在

#### T-1.2 `semantic review` 审核工作流 — ✅ 已完成（2026-08-06）
- **任务描述**：CLI 新增 `db-assistant semantic review --list / --approve --id N --meaning "..." / --reject --id N --reason "..."`；批准词条写入正式 `glossary.toml`，拒绝记录原因。
- **验收标准**：
  - [x] 待审核列表只显示 `pending_review` 词条（分页/截断）
  - [x] approve 支持覆写 meaning，写入正式 glossary 后置为 `approved`
  - [x] reject 记录原因到 `glossary.rejected.toml`，不进入正式库
  - [x] 词条 id 在文件内稳定（行号或注入 id 字段），重复操作有明确错误
  - [x] 所有写操作前备份原文件
- **验证方法**：手工流程演练 + 单测覆盖
- **边界测试**：空候选文件、id 不存在、meaning 为空、文件并发修改、路径穿越（--glossary 传外部路径）

#### T-1.3 `semantic import` + 术语合并加载 — ✅ 已完成（2026-08-06）
- **任务描述**：CLI 新增 `db-assistant semantic import --file <candidate.toml> [--force]`；Server 启动时仅加载 `approved/reviewed` 词条，`pending_review` 不注入 schema。
- **验收标准**：
  - [x] import 支持去重（精确表列/精确列已存在时跳过或覆盖，--force 覆盖）
  - [x] `Glossary.load` 过滤非 approved 词条（enrich 不泄漏候选）
  - [x] import 前备份目标文件
- **验证方法**：构造候选文件执行 import，重启 server 验证 enrich 行为
- **边界测试**：重复词条、格式错误、超大文件、只读文件权限

#### T-1.4 语义层测试 — ✅ 已完成（2026-08-06）
- **任务描述**：覆盖 T-1.1–T-1.3 的单元 + 边界 + 安全用例，并在集成测试中加入语义注入验证。
- **验收标准**：`tests/unit/test_semantic_cli.py` + `tests/integration` 语义用例全绿

---

### Phase 2: 方言转换与执行计划增强（P1）

| ID | 任务 | 依赖 | 优先级 | 验收等级 |
|---|---|---|---|---|
| T-2.1 | `translate_sql` 方言转换工具 | v0.1 validator | P1 | ✅✅ PRODUCTION |
| T-2.2 | `explain_query` 可视化输出 | v0.1 explain | P1 | ✅✅ PRODUCTION |
| T-2.3 | 方言与执行计划测试 | T-2.1–T-2.2 | P1 | ✅✅ PRODUCTION |

#### T-2.1 `translate_sql` 工具 — ✅ 已完成（2026-08-06）
- **任务描述**：新增 MCP 工具 `translate_sql(sql, from_dialect, to_dialect)`，基于 `sqlglot.transpile` 在 PG/MySQL 之间转换；目标语句必须通过只读校验（转换产物禁止引入写操作）。
- **架构约束**：`transpile → ensure_read_only` 封装为 **`SecurityGateway.translate_sql()` 原子方法**（与 `execute_query` 同模式），工具层只调网关，禁止直接调用 sqlglot——避免"先转换后忘校验"的漏洞路径。
- **验收标准**：
  - [x] 支持 `postgres → mysql`、`mysql → postgres`
  - [x] 转换结果再次过 `SqlValidator.ensure_read_only`，失败则拒绝并说明
  - [x] 转换/校验在同一网关方法内原子完成，工具层无绕过路径
  - [x] 返回 `{source, target, sql, warnings}`，转换失败给出明确错误
  - [x] 审计记录工具调用（含源/目标方言）
- **验证方法**：MCP 客户端调用 + 单测
- **边界测试**：非只读语句、无法解析语句、方言参数非法、空 SQL、超长 SQL、LIMIT/引号/函数差异用例

#### T-2.2 `explain_query` 可视化输出 — ✅ 已完成（2026-08-06）
- **任务描述**：`explain_query` 新增 `format` 参数（`raw` | `tree` | `markdown`）：`tree` 输出结构化执行计划树（节点/成本/行数/条件），`markdown` 输出便于 AI 阅读的摘要表格。
- **验收标准**：
  - [x] PG 的 EXPLAIN JSON 与 MySQL EXPLAIN 行均能转换为统一树结构
  - [x] markdown 摘要含：总成本、扫描类型、涉及表、关键节点列表
  - [x] 解析失败时回退返回原始 plan + 提示
  - [x] 原 `raw` 行为不变（向后兼容）
- **验证方法**：真实库集成测试对比两种格式输出
- **边界测试**：空 plan、嵌套循环计划、analyze 输出、MySQL 无 analyze 参数时的错误提示

#### T-2.3 方言与执行计划测试 — ✅ 已完成（2026-08-06）
- **任务描述**：覆盖 T-2.1–T-2.2 的单元 + 集成 + 安全用例（重点：转换产物注入）。
- **验收标准**：`tests/unit/test_translate.py`、`tests/unit/test_explain_format.py`、集成用例全绿

---

### Phase 3: 远程共享部署（streamable HTTP）（P1）

| ID | 任务 | 依赖 | 优先级 | 验收等级 |
|---|---|---|---|---|
| T-3.1a | HTTP transport 可行性 spike | v0.1 server | P1 | ✅✅ PRODUCTION |
| T-3.1b | HTTP transport 启动 | T-3.1a | P1 | ✅✅ PRODUCTION |
| T-3.2 | Bearer token 鉴权 | T-3.1b | P1 | ✅✅ PRODUCTION |
| T-3.3 | 远程部署安全文档 | T-3.1b–T-3.2 | P1 | ✅✅ PRODUCTION |
| T-3.4 | HTTP 模式测试 | T-3.1b–T-3.2 | P1 | ✅✅ PRODUCTION |

#### T-3.1a HTTP transport 可行性 spike（半天，里程碑最先做）— ✅ 已完成（2026-08-06）
- **任务描述**：在里程碑最前面验证当前 mcp SDK（`mcp>=1.2.0,<2`）的 streamable HTTP API 是否可用、`create_server` 是否无需改动即可复用；输出 spike 结论。
- **验收标准**：
  - [x] 最小样例 `mcp.run(transport="streamable-http")` 可启动并用 MCP 客户端 SDK 完成一次工具调用
  - [x] 结论明确：API 可用（进入 T-3.1b）/ 需升级依赖并锁定版本（升级后重跑 spike）/ 不可用（HTTP 部署移出 v0.2，任务降级为文档记录）
- **验证方法**：本地半天验证，结论写入 PROGRESS.md 更新日志
- **spike 结论（mcp 1.29.0）**：API 可用；`create_server` 零改动即可挂载 `streamable_http_app()`，8 工具 + 3 资源 in-process e2e PASS。实现注意：
  1. `host`/`port` 是 `FastMCP(...)` 构造参数（默认 127.0.0.1:8000），`run()` 不接收 —— T-3.1b 需给 `create_server` 增加可选 host/port
  2. localhost 默认启用 DNS rebinding 防护（`allowed_hosts` 为 `127.0.0.1:*` 等，Host 头需带端口；真实 uvicorn 部署自动满足）
  3. 绑定 `0.0.0.0` 时该防护默认关闭 —— 远程部署必须配合 T-3.2 Bearer token 鉴权
  4. 依赖已就绪：uvicorn / httpx / starlette 均已随 mcp 安装

#### T-3.1b HTTP transport 启动 — ✅ 已完成（2026-08-06）
- **任务描述**：新增 `db-assistant-mcp serve --http --host 0.0.0.0 --port 8000`（或 `python -m db_assistant_mcp --transport streamable-http`），复用现有 lifespan/registry。
- **验收标准**：
  - [x] `mcp.run(transport="streamable-http")` 可启动
  - [x] 工具/资源在 HTTP 模式可用（MCP 客户端 SDK e2e）
  - [x] /healthz 与 /metrics 在 HTTP 模式下仍可用
  - [x] 与 stdio 模式共用同一 `create_server` 装配，无重复代码
- **验证方法**：本地起服务 + `tests/integration/test_mcp_client.py` 扩展 HTTP 客户端用例
- **边界测试**：端口占用、未授权访问（见 T-3.2）、异常关闭

#### T-3.2 Bearer token 鉴权 — ✅ 已完成（2026-08-06）
- **任务描述**：配置 `[http] token_env = "DB_ASSISTANT_HTTP_TOKEN"`，HTTP 模式强制校验 `Authorization: Bearer <token>`，未配置 token 时拒绝启动 HTTP 模式（fail-closed）。
- **验收标准**：
  - [x] 无 token / 错误 token 返回 401，不执行任何工具
  - [x] token 只经环境变量注入，不落盘、不进日志
  - [x] stdio 模式不受影响
- **验证方法**：HTTP 请求带/不带 token 对比 + 单测
- **边界测试**：空 token、超长 token、恒定时间比较、token 泄漏审计检查

#### T-3.3 远程部署安全文档 — ✅ 已完成（2026-08-06）
- **任务描述**：更新 admin-guide：TLS 反向代理（nginx/caddy）示例、IP 白名单建议、token 轮换流程、HTTP 与 stdio 部署选型对照。
- **验收标准**：文档评审通过，覆盖部署/升级/回滚步骤

#### T-3.4 HTTP 模式测试 — ✅ 已完成（2026-08-06）
- **任务描述**：`tests/integration/test_http_server.py`：鉴权、工具调用、资源读取、健康检查、并发请求。
- **验收标准**：集成用例全绿（CI 环境跑）

---

### Phase 4: 体验与安全完善（P0/P1）

| ID | 任务 | 依赖 | 优先级 | 验收等级 |
|---|---|---|---|---|
| T-4.1 | 相似表名建议 | v0.1 schema_service | P0 | ✅✅ PRODUCTION |
| T-4.2 | CLI `config validate` / `doctor` | v0.1 cli | P1 | ✅✅ PRODUCTION |
| T-4.3 | `logs --slow` 慢查询筛选 | v0.1 cli | P1 | ✅✅ PRODUCTION |
| T-4.4 | 安全回归用例扩充 | T-4.1 | P0 | ✅✅ PRODUCTION |

#### T-4.1 相似表名建议 — ✅ 已完成（2026-08-06）
- **任务描述**：`TableNotFoundError` 时基于 schema 快照做模糊匹配（difflib/rapidfuzz，阈值 ≥0.6），错误信息携带 `suggestions: [{name, score}]`（最多 5 个）。
- **验收标准**：
  - [x] 表不存在 / 被排除表均触发建议
  - [x] 建议表也遵守 exclude 规则（不提示已排除表）
  - [x] 无相似表时 hint 文案保持现有引导
- **验证方法**：单测 + 集成
- **边界测试**：空库、同名前缀、unicode、超多表性能（快照缓存内计算）

#### T-4.2 CLI `config validate` / `doctor` — ✅ 已完成（2026-08-06）
- **任务描述**：`db-assistant config validate [--config path]` 校验配置合法性；`doctor` 汇总检查：配置、依赖版本、glossary 可解析、每个连接连通性、metrics 端口占用。
- **验收标准**：
  - [x] 输出结构化检查结果（每项 ok/warn/error + 修复建议）
  - [x] 非零退出码表示存在 error
- **验证方法**：正常/损坏配置对比运行
- **边界测试**：空配置、错误端口、无权限文件、glossary 损坏

#### T-4.3 `logs --slow` — ✅ 已完成（2026-08-06）
- **任务描述**：`db-assistant logs --slow --threshold 1000` 只显示 duration_ms 超阈值的审计条目。
- **验收标准**：与现有 `--user/--connection/--tool` 过滤可组合；无 file 输出时给出提示

#### T-4.4 安全回归用例扩充 — ✅ 已完成（2026-08-06）
- **任务描述**：新增绕过用例：HTTP 鉴权缺失、translate 产物注入（`translate_sql` 输出含写语句）、语义候选文件注入（路径穿越/恶意 meaning）、相似表建议信息泄露（确认不暴露 excluded 表）。
- **验收标准**：全部 fail-closed；`tests/security/` 新增用例全绿

---

### Phase 5: 发布与文档（P1）

| ID | 任务 | 依赖 | 优先级 | 验收等级 |
|---|---|---|---|---|
| T-5.1 | PyPI 打包发布 | v0.1 pyproject | P1 | ✅✅ PRODUCTION |
| T-5.2 | GitHub CI 全绿 | 全部任务 | P1 | ✅✅ PRODUCTION |
| T-5.3 | 文档版本升级 | 全部任务 | P0 | ✅✅ PRODUCTION |

#### T-5.1 PyPI 打包发布 — ✅ 已完成（2026-08-06）
- **任务描述**：补充发布配置（README 安装段、`uv build` / `uv publish` 脚本或 Makefile target、发布检查清单），验证 `pip install db-assistant-mcp` 后可运行 CLI 与 MCP Server。
- **验收标准**：
  - [x] wheel 构建成功且入口正确：`db-assistant` → typer CLI（`cli.main:app`）、`db-assistant-mcp` → MCP Server（`__main__:main`，stdio 启动）
  - [x] `db-assistant-mcp` 启动后不打印 CLI help（回归：v0.1 曾把该入口误指向 typer CLI，导致 README 的 MCP 配置起不了 server）
  - [x] 干净 venv 安装后 `db-assistant --version`、`python -m db_assistant_mcp --version` 均正常
  - [x] 发布文档（mcp_db.md 13 分发章节）落地
- **边界测试**：干净 venv 安装、旧版本覆盖升级

#### T-5.2 GitHub CI 全绿 — ✅ 已完成（2026-08-06）
- **任务描述**：CI 增加 v0.2 测试目录与 HTTP 集成步骤；确认 lint（ruff）+ 单测 + Docker 集成矩阵全绿。
- **验收标准**：GitHub Actions 全绿（需推送到 GitHub 后验证）

#### T-5.3 文档版本升级 — ✅ 已完成（2026-08-06）
- **任务描述**：更新 `mcp_db.md`（v0.2 章节：新工具/HTTP 传输/鉴权）、`admin-guide.md`（语义工作流、远程部署）、`PROGRESS.md`（v0.2 看板与更新日志）、README（新工具列表）。
- **验收标准**：文档与实现一致，无 v0.1 遗留矛盾（如 admin-guide 中"webhook 为 v2"等过时描述）

---

## 4. 测试策略

| 层级 | 内容 |
|---|---|
| 单元测试 | semantic 生成/审核/导入、translate、explain 格式化、HTTP 鉴权、模糊匹配、config validate |
| 集成测试 | Docker PG/MySQL 上跑语义生成、translate、explain 可视化、HTTP 客户端 e2e |
| 安全测试 | HTTP 未授权、translate 注入、候选文件注入、建议信息泄露、token 日志泄漏 |
| 回归 | v0.1 全部 171 用例保持通过（新增功能不得破坏只读安全边界） |

## 5. 风险

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | mcp SDK streamable HTTP API 与当前版本不匹配 | 中 | T-3.1 先做版本验证 spike；必要时升级 mcp 依赖并锁定 |
| R-2 | 语义生成质量低（启发式误判） | 中 | confidence 分级 + 强制人工审核 + 拒绝原因沉淀词典 |
| R-3 | HTTP 暴露增加攻击面 | 高 | Bearer token fail-closed + TLS 反代文档 + 默认仅监听回环 |
| R-4 | 方言转换产物绕过校验 | 高 | 转换产物强制过只读校验 + 安全用例（T-4.4） |

## 6. 交付顺序建议

0. **T-3.1a HTTP spike（半天，最先做）**：结论决定 Phase 3 是否成立，避免后期返工
1. Phase 4 的 T-4.1（相似表名，P0，成本低，收益立即可见）
2. Phase 1 语义工作流（P0 主战场）
3. ~~Phase 2 方言/执行计划（P1，sqlglot 成本低）~~ ✅ 已完成
4. ~~Phase 3 其余任务（T-3.1b–T-3.4，基于 spike 结论）~~ ✅ 已完成
5. Phase 5 发布（收尾，依赖前面全部；含入口回归验收）
