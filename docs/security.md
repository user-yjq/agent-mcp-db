# 安全设计实现说明

## 分层防护

1. **只读解析校验（sqlglot）**
   - 仅放行 `SELECT / WITH / EXPLAIN / SHOW`（`EXPLAIN` 需递归校验内部查询）。
   - 拒绝多语句、嵌套写入（CTE 中 INSERT 等）、事务控制、`SET`、`USE`、`CALL`、`COPY`、`SELECT INTO`。
   - 危险函数黑名单：`pg_read_file`、`pg_sleep`、`LOAD_FILE`、`sleep`、`benchmark`、`sys_eval` 等。
   - 解析失败即拒绝（fail-closed）；客户端只收到通用拒绝信息，具体原因仅入审计。
2. **资源限制**：自动 LIMIT（默认 100，最大 1000）、查询超时（默认 10s）、并发上限（默认 5）。
3. **脱敏与排除**：`masked_columns` 打码为 `***`；`exclude_columns` / `exclude_tables` 从 schema 与结果中隐藏；敏感列名（password/token/secret/phone/id_card 等）自动打码；支持 `表名.列名` 限定与别名解析。
4. **审计**：每次工具调用记录 ts/client/user/tool/connection/sql/rows/duration_ms/allowed，成功与拒绝均记录；webhook 失败不影响主流程。
5. **凭据**：不落盘明文；支持 `password_env` 环境变量引用或 AES-256-GCM 加密存储（主密钥来自 `DB_ASSISTANT_MASTER_KEY` 或 `~/.config/db-assistant/master.key`）。

## 已知偏差

- PROGRESS.md 中 SEC-017 预期小写 `select` 被拒绝，本实现基于真实解析器将大小写规范化后仍视为只读放行（设计文档 6.2 明确“解析层优先使用真实解析器，不依赖正则”）。小写绕过不会绕过校验——它仍会被识别为 SELECT 并按只读处理。

## 威胁模型对照

| 威胁 | 缓解 |
|---|---|
| AI 生成破坏性 SQL | 解析器校验 + 默认只读 + 分档 mode |
| 恶意提示词诱导越权 | 权限仅与配置绑定，与对话内容无关 |
| 长查询拖垮数据库 | 行数上限 + 超时 + 并发限制 |
| 凭据泄露 | 加密存储 + 日志脱敏 + 环境变量注入 |
| 敏感数据外发 | 服务端脱敏；schema 快照排除敏感表/列 |

