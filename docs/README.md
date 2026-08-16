# 文档索引

按“当前决策优先、历史按需展开”组织。

## 当前产品（默认阅读）

- `product/04_MVP_CANDIDATE_PRD.md`：目标、边界、评分规则与 DoD。
- `product/05_MVP_SCENARIO_ACCEPTANCE.md`：真人场景验收表。
- `product/06_MVP_COMPLETION_AUDIT.md`：当前完成度和权威证据。

## 支撑材料（按需阅读）

- `decisions/`：ADR 与关键工程决策。
- `architecture/`：早期调研、架构和阶段规划；仅供追溯，不覆盖当前 PRD。
- `research/`：产品与视觉研究。
- `audits/`：历史项目审计。
- `screenshots/`：可长期复查的产品视觉证据。
- `history/`：详细阶段日志和旧 AGENTS 内容；不作为当前实现指令。

## 过程产物

运行日志、临时 SQLite、浏览器快照、一次性 verifier 输出统一放 `.harness/`，完成或失败后清理，不提交到仓库。稳定性结论必须沉淀回 `product/06_MVP_COMPLETION_AUDIT.md`。

