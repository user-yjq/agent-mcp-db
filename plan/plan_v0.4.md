# v0.4 实施计划

| 项 | 内容 |
|---|---|
| 文档版本 | v0.4（规划稿） |
| 创建日期 | 2026-08-09 |
| 目标里程碑 | v0.4：schema 语义与复杂查询能力增强 |
| 优先级定义 | P0 必须交付 / P1 建议交付 / P2 可选 |

---

## 1. 规划依据

v0.3（C-1~C-3）交付后，Codex 实测关注点转移到「AI 能否直接理解复杂库结构并给出答案」。当前缺口：

1. **表注释读取不完整**：PG `list_tables` 不读表注释（`comment` 恒为 `None`），MySQL `table_schema` 不读表注释（写死 `None`）——schema 上下文缺业务语义
2. **复杂结构查询能力未系统性验证**：JSON 字段、视图、递归 CTE 等结构在 PG/MySQL 双库的「validator → gateway → 驱动 → 返回」全链路缺少盘点与实测（演示库已就绪，可量化验证）
3. **glossary 只支持词条精确/pattern 匹配**：无同义词/别名，中文习惯用词（如「用户」「顾客」）无法互相命中

**明确不做**：写操作（safe_write/full 模式继续推迟）、RBAC、多租户（沿用 v1/v2 规划）。

---

## 2. 任务清单

| ID | 任务 | 优先级 | 状态 |
|---|---|---|---|
| D-1 | 表注释读取补齐：PG `list_tables` 读 `obj_description`；MySQL `table_schema` 读 `TABLE_COMMENT`；演示库 seed 增加表/列注释；CI 集成 job 先跑 seed 再测试 | P0 | ⬜ 进行中 |
| D-2 | 复杂查询能力盘点补齐：用演示库实测 JSON（PG `?`/`->>`、MySQL `JSON_EXTRACT`）、视图、递归 CTE 在双库全链路可用；发现的缺口逐个修复并加回归 | P1 | ⬜ 待启动 |
| D-3 | glossary 增强：词条别名/同义词支持（如「用户」↔「顾客」），命中时统一归一化输出 | P2 | ⬜ 待启动 |

---

## 3. D-1 验收标准

- PG：`list_tables` 返回演示库 `users` 表注释（中文语义）；`table_schema` 注释与列注释一致透出
- MySQL：`table_schema` 返回演示库 `users` 表注释与 `orders.order_status` 列注释
- CI 集成 job 初始化演示数据后，双库注释集成测试通过（真库断言）
- 全量单测 + ruff 通过，无回归
