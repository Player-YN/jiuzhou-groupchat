# 九洲一号群：可测试的 MVP Candidate

## 产品命题

一个真人进入由 6 个固定小说角色组成的持续群聊。核心价值不是“多 Agent 讨论”，而是角色会因语境、关系、个性和未完成事项产生不同的说话动机，也会自然沉默。

## 本阶段范围

- 事件类型：用户消息、NPC 消息、关系变化、承诺到期、世界事件、空闲 tick。
- 一次事件只进行一次六角色批量语义评估。
- LLM 输出 0～3 的语义分项，不输出最终发言者。
- 程序执行硬过滤、加权评分、阈值判断、冷却、预算和多人仲裁。
- 普通事件允许 0 人回复，最多 2 人回复；主动空闲事件最多 1 人回复。
- 明确 `@角色` 有确定性选择兜底；DM 始终由目标角色响应。
- 自主连续对话最多 3 跳；相同事件只能处理一次。
- 每次决策保存完整输入、分项、策略、分数和结果，可通过 API 重算校验。
- 每次语义评估记录模型标识、提示词哈希、耗时、候选数及 `ok/mock/timeout/invalid/error` 状态。

## 在线评分

```text
semantic = 0.24 relevance
         + 0.20 social_obligation
         + 0.14 relationship_motivation
         + 0.14 continuity
         + 0.10 persona_impulse
         + 0.18 novelty_potential

final = semantic + deterministic adjustments
```

默认回复阈值为 `0.60`。第二位角色必须与第一位相差不超过 `0.12`、`novelty_potential >= 2`，且 `contribution_key` 不同。明确 @ 的角色获得 `0.90` 分下限，但仍受 mute、sleep 和每日预算等硬限制。

本 Candidate 尚无轻量 reaction 的前端协议；LLM 输出 `proposed_action=react` 时记录意图但不升级为完整发言。

## 失败语义

- 语义模型超时、非法 JSON 或缺少任何角色：普通事件默认沉默。
- 明确 @：即使语义评估失败，规则仍选择被 @ 角色。
- 相同 event ID + 相同输入：作为重试抑制，不重复写记忆或生成。
- 相同 event ID + 不同输入：报告 `EVENT_ID_COLLISION`。
- 生成失败：输出错误事件，不自动扩大到其他角色。
- 明确 @ 或 DM 在 Letta 与备用 provider 都失败/超时时，返回角色化短句兜底；普通未点名事件不使用罐头回复。
- 单次生成总时限默认 90 秒，最终文本硬上限默认 600 字，防止模型无止境流式输出。

## 审计接口

- `GET /api/behavior/decisions/{event_id}`：查看一次完整决策。
- `GET /api/behavior/decisions?session_id=...`：查看会话决策列表。
- `POST /api/behavior/decisions/{event_id}/replay`：只重跑确定性规则并比较结果。
- `POST /api/cron/trigger {"service":"behavior","behavior_event_type":"idle_tick|world_event|relationship_change|promise_due|npc_message","text":"..."}`：注入可测试事件。

## Definition of Done

1. 群聊默认路径不再固定轮询 6～8 次。
2. 普通消息能自然沉默，且任何普通事件最多 2 位响应者。
3. 主动行为由单一协调器批量评估，不运行六个并行评估 loop。
4. `@`、DM、幂等、三跳停止、冷却和每日预算有自动化测试。
5. 决策日志可查询并确定性回放，回放结果逐字段一致。
6. 后端全量测试、前端类型检查和构建通过。
7. 24 小时 soak 中无重复消息、无限链、预算穿透、任务死亡或服务崩溃。
8. 完成真人场景验收后，才进入 MVP 用户测试；本阶段不实现离线 LLM-as-Judge。

真人验收用例与阈值见 `05_MVP_SCENARIO_ACCEPTANCE.md`。

## 明确停止扩张

不加入多真人群聊、战斗/养成系统、自动剧情导演、生产数据库迁移、大规模 UI 重做或离线 LLM-as-Judge。达到 DoD 后停止功能开发，以真人试玩数据决定下一阶段。
