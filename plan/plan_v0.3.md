# v0.3 实施计划

| 项 | 内容 |
|---|---|
| 文档版本 | v0.3（规划稿） |
| 创建日期 | 2026-08-07 |
| 目标里程碑 | v0.3：AI 体验完善（Codex 接入实测反馈驱动） |
| 优先级定义 | P0 必须交付 / P1 建议交付 / P2 可选 |

---

## 1. 规划依据（Codex 接入实测反馈，2026-08-07）

db-assistant-mcp 已接入 Codex（`codex mcp add db-assistant`，stdio 模式），实测 9 个工具全链路可用。实测发现的体验缺口：

1. **SQL 报错时 AI 看不到原因**：复杂查询（如递归 CTE 类型不匹配、列不存在）只返回 `INTERNAL_ERROR` + 通用 message，真实 DB 报错仅进审计日志，模型无法自我修正
2. `search_schema` 只做列名字符串匹配，未接入 glossary 中文语义（搜"订单/时间"返回空）
3. `[audit] output = "stdout"` 在 stdio 模式下会向协议流打印（仅拒绝/错误路径触发），存在污染风险（已用 file 模式规避）

**明确不做**：写操作（safe_write/full 模式继续推迟）、RBAC、多租户。

---

## 2. 任务清单

| ID | 任务 | 优先级 | 状态 |
|---|---|---|---|
| C-1 | 数据库错误明细透出给 AI：`AppError.to_dict()` 截断透出 detail；`execute_query`/`explain_query` 的 DB 异常包装为「类型 + 单行消息」（≤300 字符）并带自纠 hint；审计同步记录 | P0 | ✅ 已完成（2026-08-07） |
| C-2 | `search_schema` 接入 glossary：中文语义词命中对应表/列（含 pattern 匹配） | P1 | ⬜ 待办 |
| C-3 | 审计 stdout 模式防护：stdio 传输下禁止 stdout 输出（降级/告警），避免污染 JSON-RPC 流 | P2 | ⬜ 待办 |

---

## 3. C-1 验收记录（2026-08-07）

- `AppError.to_dict()` 在 detail 存在时返回截断版（`AI_DETAIL_MAX=300`），无 detail 时字段缺省
- 坏 SQL 实测：`execute_query` 递归 CTE 类型错误 → `detail: DatatypeMismatchError: recursive query "tree" column 5 has type character varying(100)[] ...`；`explain_query` 列不存在 → `detail: UndefinedColumnError: column "foo" does not exist`
- 安全回归：SecurityRejectedError 的 message 保持通用（不暴露原始语句），规则编码经 detail 透出；excluded 表名不泄露测试通过
- 测试：`test_errors.py`（3 例）、`test_gateway.py`（+2 例）、`test_sql_validator.py` 更新 1 例；全量 **399 passed / 5 skipped / 0 failed**，ruff 全过
