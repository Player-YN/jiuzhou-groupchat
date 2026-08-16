# MVP Candidate 完成审计

> 本文件按原始 Goal 逐项记录权威证据。状态只能是 `PROVED / PENDING / FAILED`；没有证据不得写完成。

| 要求 | 状态 | 权威证据 |
|---|---|---|
| 事件驱动行为输入 | PROVED | `BehaviorEvent` 支持 user/NPC/relationship/promise/world/idle；admin trigger 可注入非用户事件 |
| LLM 只做结构化语义判断 | PROVED | `assess_intents()` 一次输出六角色离散分项；缺项、重复角色、非法 JSON、超时均返回空评估 |
| 确定性最终评分与仲裁 | PROVED | `BehaviorEngine.decide()` 为纯函数；相同输入重算逐字段一致 |
| 普通事件可以自然沉默 | PROVED | 低于 `0.60` 不选择角色；群聊集成测试验证 0 个 `agent_done` |
| 普通事件最多两名响应者 | PROVED | 引擎 cap=2；群聊即使传 `max_rounds=8` 仍只生成两位 |
| 主动行为避免六次独立评估 | PROVED | lifespan 默认启动单一 `BehaviorCoordinator`，旧六 loop 不自动启动；一次事件测试只调用一次 batch assessor |
| 决策可审计 | PROVED | SQLite 保存 event、intent/policy inputs、分数、调整、理由和结果，并记录评估模型、prompt hash、延迟与 ok/mock/timeout/invalid/error 状态；REST GET 可查 |
| 决策可回放 | PROVED | replay 用日志原始输入重跑纯规则并逐字段比较；API 与测试覆盖 |
| 明确 @ 确定性兜底 | PROVED | 评估为空时仍给被 @ 角色 0.90 floor 并选择；集成测试覆盖 |
| DM 确定性目标 | PROVED | DM msg 构造明确目标事件，规则必须只选择目标角色；双 provider 故障/空输出仍有角色化短句兜底 |
| 群聊重复触发保护 | PROVED | 相同 msg_id 重发在记忆写入前停止；相同 ID 不同输入返回 collision |
| DM 重复触发保护 | PROVED | DM 前端发送 msg_id；后端 duplicate ack；测试验证只生成、持久化一次 |
| 失控连锁保护 | PROVED | chain_depth >= 3 返回 `chain_stopped`；主动 idle 每事件最多一人，系统不自动无限续写 |
| 单次输出失控保护 | PROVED | 在线群聊、DM、主动协调器共享总生成超时与 600 字硬上限；超时的直接召唤走角色短句兜底 |
| 冷却与主动预算 | PROVED | coordinator 将 60 秒 cooldown、5 分钟 recent penalty、每日角色预算传入硬策略 |
| 主动策略单一真相 | PROVED | 新协调器启用时旧随机 cron 保持 dormant；disabled、npc_filter、无人在线零调用均进入运行路径 |
| DM 当前问题不重复进入模型 | PROVED | 已持久化的最后一条 DM user event 不再二次 append；捕获模型输入测试验证一次 |
| 后端自动化 gate | PROVED | 当前冻结版全量：Ruff `app + tests` clean，pytest **138/138 PASS** |
| 前端 gate | PROVED | Next.js production build + TypeScript 检查通过（16.6 kB / 122 kB），主动消息与 DM msg_id 类型已接入 |
| 真实浏览器 smoke | PROVED | 实际页面验证 `@白前辈` 返回“嗯。善。”角色文本且不串成宋书航；内部 supervisor/done 不显示；药师 DM 正确回应并写入独立记忆 |
| 24 小时稳定性 | PENDING | 2026-07-15 21:45 的隔离 runner 运行 8040 turns 后跨越午夜失败：runner 错把每日预算当作跨自然日永久关闭；协调器按日期清空 `daily_counts` 属预期产品语义。该次运行不构成产品预算穿透证据，也不构成通过证据。需修正 runner 为“每个自然日分别验证额度”，从零重跑完整 24h。此前预检仍已覆盖 0/2 响应、六角色身份、duplicate/replay、depth-3 停止、协调器存活和 legacy cron 休眠。 |
| 真人场景验收 | PENDING | 用例和阈值已冻结于 `05_MVP_SCENARIO_ACCEPTANCE.md`，尚无真人结果记录 |

## 当前结论

代码已达到 MVP Candidate 的工程候选状态，但完整 Goal 尚未完成。剩余停止条件是：修正跨日预算断言后完整 24 小时长跑通过，以及真人场景验收通过；任何一项失败都必须修复并重新验证。
