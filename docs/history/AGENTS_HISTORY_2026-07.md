# AGENTS.md - 项目 B：六角色持续群聊社交游戏

> **AI Agent 项目档案**。每次进入这个项目前先读这个文件。写完代码后必须更新本文件（进度 + 踩坑 + 决策）。

## 项目目标

**单个真人用户 + 6 个固定小说角色 AI** 的持续群聊社交游戏。角色根据语境、关系、个性和未完成事项产生行为意图，可以说话也可以自然沉默；用户可在群聊中 @ 角色或进入 DM。产品价值是“像一个持续存在的真人群”，不是多视角讨论、头脑风暴或会议纪要。

## 技术栈

- **后端**：FastAPI + Uvicorn + WebSocket
- **多 Agent 编排**：LangGraph（StateGraph + Supervisor pattern）
- **LLM**：OpenAI GPT-4o（主） + DeepSeek（备，限流时降级）
- **状态持久化**：InMemorySaver（MVP）→ PostgresSaver（生产）
- **前端**：Next.js 15（App Router）+ Tailwind + shadcn/ui + TypeScript
- **数据库**：SQLite（MVP）→ Postgres（生产）
- **部署**：Docker Compose（backend + frontend + 可选 Ollama）

## 目录约定

```
项目B_GroupChat/
├── AGENTS.md                    ← 本文件
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
├── 01_调研报告.md
├── 02_架构设计.md
├── 03_阶段规划.md
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py              ← FastAPI 入口 + WS endpoint
│   │   ├── graph.py             ← LangGraph StateGraph
│   │   ├── agents/
│   │   │   ├── host.py
│   │   │   ├── creator.py
│   │   │   ├── critic.py
│   │   │   └── summarizer.py
│   │   ├── routers/
│   │   │   ├── ws.py            ← WebSocket 处理
│   │   │   └── summary.py
│   │   └── models.py            ← Pydantic schemas
│   └── tests/test_graph.py
├── frontend/
│   ├── package.json
│   ├── app/
│   │   ├── page.tsx
│   │   └── layout.tsx
│   ├── components/
│   │   ├── ChatRoom.tsx
│   │   ├── MessageBubble.tsx
│   │   ├── TopicInput.tsx
│   │   ├── SummaryCard.tsx
│   │   └── StreamingText.tsx
│   └── hooks/useWebSocket.ts
└── tests/
    ├── backend/
    └── e2e/chat.spec.ts
```

## 当前阶段

**MVP-Candidate-Behavior — 事件驱动行为评分与群聊社交游戏化（进行中，未完成 24h soak）**

- 产品目的已纠正为“真人用户 + 6 个固定小说角色的持续群聊社交游戏”，不再以多视角头脑风暴为当前目标。
- 新增 `backend/app/behavior/engine.py`：六角色单次批量语义评估、0～3 离散分项、确定性加权、硬过滤、最多两位响应、自然沉默、明确 @ 兜底、三跳停止和 event ID 幂等。
- `backend/app/graph.py::stream_group_chat` 已退出默认 8 轮固定轮询，改为一次事件选择 0～2 位角色后串行流式生成。
- 新增 SQLite `behavior_decisions` 日志和 `/api/behavior/decisions*` 只读审计/确定性 replay API；日志保存原始语义输入、运行态策略、最终分数与选择理由。
- 新增 `backend/app/scheduler/behavior_coordinator.py`：默认主动行为由一个协调器处理，一次 idle/world 事件只批量评估一次；旧 6 个 `NpcLoop` 保留手动兼容但不再由 lifespan 默认启动。
- 前端既有 `msg_id` 已接入后端作为稳定 event ID；重复重连不会二次写记忆或二次生成；ID 与不同输入冲突会报错。
- 修复 `backend/app/main.py` shutdown 未 await `shutdown_scheduler()` 的异步生命周期问题。
- 规格与停止条件见 `04_MVP_CANDIDATE_PRD.md`。
- 当前冻结版 gate：Ruff `app + tests` 全清；pytest **138/138 PASS**；Next.js production build + TypeScript PASS（16.6 kB / 122 kB）。
- DM 追加修复：前端 `dm_msg.msg_id` → 后端 append-once 决策日志；同 ID 同输入 duplicate ack，同 ID 不同输入 collision；同时修复“ws 已持久化当前问题，stream_dm_chat 又追加一次”导致模型看到重复问题。
- 审计元数据补齐：每次 batch assessment 保存 model label / prompt hash / latency / candidate count / status；超时、重复角色、缺角色与非法输出全部降级为普通事件沉默。
- 主动协调器限流补齐：disabled 开关、npc_filter、无人在线零模型调用、每日预算均进入实际运行路径并有测试，不再只是策略模型里的未接字段。
- 主动策略冲突修复：`GC_LOOPS_ENABLED=true` 时 legacy `XiuzhenCronService` 不启动 interval job，避免随机 cron 与行为协调器形成两套主动发言真相。
- 直接响应韧性：明确 @ / DM 在 Letta + provider 双失败、空输出或总生成超时时使用每角色极短 fallback；普通消息不使用罐头。所有在线生成增加 90s 总 deadline + 600 字硬上限。
- 新增 `05_MVP_SCENARIO_ACCEPTANCE.md`（34 条真人验收场景）与 `06_MVP_COMPLETION_AUDIT.md`（逐要求 PROVED/PENDING 证据矩阵）。
- 新增 `backend/tests/soak_mvp_candidate.py`：通过真实 FastAPI WebSocket 跑 mock-LLM 实时 soak，覆盖 0～2 响应、流式协议、SQLite 记忆/决策日志和重复事件抑制。5 秒预检完成 20 turns / 2 duplicate checks / 0 error / 0 violation。
- 20:36 的预跑因冻结前继续修改行为代码而主动终止，不作为最终证据。
- 真实浏览器 smoke 已验证：`@白前辈` 返回白前辈专属文本、内部 supervisor/done 事件不再显示、药师 DM 身份与独立记忆正确。
- 多轮证据审计先后补齐角色串台、0/2 响应、主动预算闭合和长跑内三跳停止探针，所有较弱运行均明确废弃。最终 runner 将主动预算设为 3：前三次白前辈探针必须 push，之后持续 silent 且 `daily_counts <= 3`；每 100 turns 注入 `chain_depth=3` NPC 事件并要求 `silent + max_chain_depth_reached`。强化版 2 秒预检覆盖 61 turns、7 次沉默、6 次双响应、六角色、主动 push、chain stop、duplicate/replay/DM，0 error / 0 violation。当前权威 24 小时 soak 已于 **2026-07-15 21:45:56** 从隔离 SQLite 数据库启动；完成前 Goal 保持 active。

### 2026-07-15 MVP Candidate 行为引擎（进行中）

- 决策：在线不使用 LLM-as-Judge。LLM 只做批量语义特征提取；规则代码掌握发言权。离线 Judge 本阶段不做。
- 踩坑：旧 `AgentMemoryStore.fan_out_group_event` 会为 6 个 audience 写 6 条物理行；主动协调器构造上下文前必须按 `(timestamp, speaker_key, text)` 去重，否则模型会把同一句话读六遍。
- 踩坑：只保存最终分数不构成“可回放”。必须同时保存 `intent_inputs`、`policy_inputs` 和 `max_responders`，再用纯规则函数重算并逐字段比较。
- 踩坑：幂等不能只检查 event ID 是否存在；同 ID 不同输入是碰撞，应返回错误，不能静默当作重试。
- 决策：主动 idle tick 最多 1 位角色，用户普通消息最多 2 位；两者共享同一评分器，避免形成两套行为真相。
- 踩坑：角色 prompt 的身份锚点里会提到“你不是其他五人”；mock 若按全文第一个角色关键词匹配，会把白前辈误判成宋书航。测试替身也必须优先解析唯一身份锚点，并为六个角色各做回归断言。
- 踩坑：只统计 `rounds <= 2` 不能证明长跑覆盖了自然沉默和双响应；soak 必须分别记录 `silent_turns` / `two_responder_turns` 并在结束时要求二者非零。长跑还需逐条校验 `agent_done` 的角色 key 与专属文本 marker，才能捕获“路由是白前辈、正文却是宋书航”的串台。
- 踩坑：只在 `/api/cron/status` 断言 `daily_counts <= daily_budget` 可能永远是 0，不能证明预算真的会关闭。稳定性 runner 应在隔离环境用小预算主动打满同一角色，并在余下运行期持续断言超额触发为 silent。
- 踩坑：三跳停止只有 unit/integration test 仍不足以支撑 DoD 中“24h 无无限链”的字面要求；长跑必须周期注入 depth-3 NPC 事件并直接断言停止原因。

**B-Session-UX-Cleanup — T9 session 体验修复包 + T10 cleanup 包（pytest 98/98 PASS，HEAD `0feb77a`，git status clean）**

- 任务清单：见 `03_阶段规划.md` B-Session-UX-Cleanup 节
- **T9 commit (a)** `9bcf9c6` `feat(B-session): DM sessionId localStorage 持久化 — module cache 跨刷新救场`：
    - `frontend/lib/dmSessionId.ts` — `getDmSessionId(target)` / `setDmSessionId(target, id)` / `clearDmSessionId(target)` 三件套，key=`dm-session-id:<target>` per-target
    - `frontend/lib/ws.ts` — `getDmSessionId()` 优先 localStorage，fallback module cache
    - `frontend/hooks/useDmSession.ts` — 启动时用 localStorage 持久 ID，连接断开不重置
- **T9 commit (b)** `b8c1adb` `feat(B-identity): 「神秘人」默认 user ID + 可改 — author 字段全链路透传`：
    - `frontend/lib/userIdentity.ts` (新) — `getUserIdentity()` / `setUserIdentity(name)` 双存储 (localStorage + module cache)，默认 "神秘人"
    - `frontend/app/page.tsx` + `frontend/components/ChatRoom.tsx` + `frontend/components/GroupSidebar.tsx` + `frontend/components/DMWindow.tsx` — 群聊 + DM header 加 ✎ 按钮 inline 编辑
    - `backend/app/models.py` — `AgentMemoryEntry` model + `user_msg` schema 加 `author: str | None` 字段
    - `backend/app/memory/agent_memory.py` — SQLite `author TEXT` 列 + ALTER-TABLE 迁移（给老 user rows backfill "神秘人"）+ `load_history` 透传
    - `backend/app/graph.py` + `backend/app/routers/ws.py` + `backend/app/routers/group_history.py` — `user_msg` / `dm_msg` payload 读 `author` → 透传到 history + `ChatBubble` 渲染 "我：{author}"
    - `backend/tests/test_agent_memory.py` + `backend/tests/test_dm.py` — 5 个新 author 测试
- **T9 commit (c)** `628a882` `feat(B-clear): 每窗口 Clear 按钮 — DELETE 后端 + 清 localStorage + 不影响其他窗口`：
    - `backend/app/routers/history_delete.py` (新) — `DELETE /api/group/history?session_id=X` + `DELETE /api/dm/history?session_id=X&agent_key=Y`
    - `backend/app/memory/agent_memory.py` — `delete_session(session_id, agent_key=None)` 删 group session 或单个 (session_id, agent_key) DM 桶
    - `frontend/components/ChatRoom.tsx` + `frontend/components/DMWindow.tsx` — 加 🗑 按钮，confirm() 调 DELETE + `clearLocalHistory()` + `reconnect({forceNewSession: true})`
- **T9 commit (docs)** `0bb9a8b` `docs(B): AGENTS.md backfill — Stage 8+ T9 session 体验修复包` — 同步 AGENTS.md 踩坑笔记 + 上次更新
- **T9 Eval gate**：pytest 92/93 PASS (1 pre-existing flake `test_ws_dm_full_flow` 隔离重跑 PASS in 5.13s，与 T9 无关) + tsc clean + vite build 16.6kB/122kB + OpenAPI 含两个 DELETE endpoint + live SQLite `delete_session` 三阶段验证 group+dm 互不干扰
- **T10 commit (a)** `f456033` `chore(B-cleanup): drop sidebar hover 2px 上浮特效`：
    - `frontend/components/GroupSidebar.tsx:98` `transition-all hover:-translate-y-0.5 ...` → `transition-shadow hover:shadow-lg ...`（去掉位置,保留 border + shadow hover 效果）
- **T10 commit (b)** `644f47e` `chore(B-cleanup): git rm 25 中间脚本/log/report — 用户要求清理项目目录`：
    - Backend scripts (1) `backend/scripts/fixup_xiuzhen_npc_agents.py` + probe tests (2) `backend/tests/probe_dm_e2e.py` / `probe_letta_e2e.py`
    - Frontend scripts (7) `frontend/scripts/{bug3-layout-verify, screenshot, screenshot_stage5, screenshot_stage5_static, screenshot_stage8_beautify, screenshot_stage8b_beautify, stage6-dm-smoke}.cjs`
    - Backend STAGE/FIX/VERIFICATION/REPORT .md (9) + Frontend STAGE*.md (2) + Desktop-launcher (1) + Internal reports (3) = 25 tracked git rm
    - 额外 mavis-trash 41 untracked（17 backend .log + 1 backend md + 11 frontend .log + 2 root .log + 6 launcher .log + 4 root .harness 文件）
    - `.gitignore` 加 18 行显式 log patterns
- **T10 commit (docs)** `0feb77a` `docs(B): AGENTS.md backfill — T10 Cleanup 包` — 同步 踩坑笔记 + 上次更新
- **T10 Eval gate**：pytest **98/98 PASS** (44.00s, 0 fail) + tsc clean + npm run build PASS (16.6kB/122kB, no regression) + tracked files 193 → 168 精确少 25 + hard rules 100% 守
- **下一步**：A.P3 长期记忆（用户主项目 A 推进中）；项目 B 待 A.P3 同步后启 cron 主动行为实战验证

## 当前阶段（旧 — 保留便于回溯）

- 任务清单：见 `03_阶段规划.md` Stage 7 节
- **Stage 7 Letta 集成 commit (a)** `e19f3eb` `test(backend): Letta v0.16.8 integration tests + bridge module skeleton`：
    - `backend/app/letta_bridge/__init__.py` — public API re-exports
    - `backend/app/letta_bridge/letta_client.py` — async HTTP wrapper around Letta REST API (`/v1/health`, `/v1/agents`, `/v1/agents/{id}`, `messages/stream` SSE)，含 `set_test_transport` 测试 hook
    - `backend/app/letta_bridge/role_seeds.py` — 把 `ROLES` dict 翻译成 Letta memory-block payload + archival seed passages（6 九洲一号群角色）
    - `backend/app/letta_bridge/agent_manager.py` — SQLite-backed `role_key → agent_id` registry + `bootstrap_all`（idempotent: created/reused/recovered/failed outcome per NPC）
    - `backend/app/letta_bridge/singleton.py` — process-wide LettaClient singleton + test helpers
    - `backend/tests/test_letta_integration.py` — 6 个测试（MockTransport，不需要真 Letta 库） 0.27s 全过
- **Stage 7 Letta 集成 commit (b)** `5fdfcc8` `feat(stage7-letta): wire Letta v0.16.8 into BFF`：
    - `backend/app/config.py` — Settings 加 `letta_base_url` / `letta_api_key` / `letta_llm_model` / `use_letta`，默认 `use_letta=true`
    - `backend/app/graph.py` — `_stream_via_letta()` async generator + `_use_letta_path()` predicate；`make_agent_node` + `stream_dm_chat` 都 try/except graceful degrade 到 per-role provider（mock 路径永远不被覆盖）；失败时 emit `letta_fallback` / `dm_error{code: LETTA_FALLBACK}` 事件
    - `backend/app/main.py` — lifespan hook 调 `bootstrap_all`（创建 6 NPC），`/api/health` deep probe 返 `letta.status + letta.agents[]`，shutdown 时 aclose LettaClient
    - `backend/tests/probe_letta_e2e.py` — 6 步 real e2e probe（健康 / 群聊 / DM 接入 / DM 流 / 持久化）
    - `docker-compose.yml` — 加 `letta` + `postgres` service，healthcheck + 启动顺序
    - `.env.example` — 文档化 `LETTA_*` + `USE_LETTA` + Ollama handle 约定
    - `restart_uvicorn.{bat,ps1}` — 重启 uvicorn 加载新 lifespan 的 helper
- **Stage 7 Letta 集成 commit (c)** `d7ec152` `fix(tests): probe_letta_e2e splits into Phase A (group) + Phase B (DM) WS`：
    - 把 probe 切成 Phase A (group, steps 2-3) + Phase B (DM, steps 4-6)，符合 §Stage 6 DM Phase 2 的"DM ↔ 群聊切换需重连"架构
    - 同一 session_id (URL) 让 AgentMemoryStore 跨重连保留 (session_id, agent_key) 历史
- **Eval gate**：pytest 28/28 PASS (6.51s) — 含 6 个新增 Letta 测试；ruff clean on 新文件；/api/health 返 `letta.status: up` + 6 NPC agents；group chat via Letta leaf e2e OK（4 chunks）；DM 路径 graceful degrade（见下）
- **下一步**：Stage 8 九洲一号群个性化增强 / per-NPC persona refine / Postgres 化 registry
- **Stage 8-NPC-Love commit** `feat(B-scheduler)`: per-NPC 自主 loop — ADR-0007 Option B（"我想插一句话" 行为）
    - 新模块 `backend/app/scheduler/npc_loop.py` — `NpcLoop` dataclass + `NpcLoopPool` (start_all/stop_all/trigger_one) + `_npc_loop` 协程 (think→decide→act→sleep→revive) + `_one_cycle` 返回 float 睡眠秒数 + `_one_cycle_summary` 测试用同步包装；session_id `loop-{role_key}`（与旧的 `cron-` 区分）；决策 prompt 含 20 个最近群事件 + persona + `<silent/>` token；WS 事件 `cron_agent_post` 加 `source="npc_loop"` tag
    - 新模块 `backend/app/scheduler/group_semaphore.py` — `GroupChatSemaphore` (asyncio.Semaphore(1) + 10s 冷却)，懒绑定 loop 防止测试 fixture 中报错；`guard()` async context manager yield bool (False = 冷却激活)
    - 新模块 `backend/app/scheduler/letta_retry.py` — `stream_via_letta_with_retry` async generator 包装 `_stream_via_letta`，1s/2s/4s 指数退避 + 0..1s jitter，最多 3 次重试后 raise `LettaRetryExhausted`（loop sleep 5min）；按 class name + status_code=429 判定避免 import cycle
    - 修改 `backend/app/scheduler/lifespan.py` — `SchedulerBundle.npc_loop_pool` 字段；`_read_loops_enabled_env()` 读 `GC_LOOPS_ENABLED` (默认 true)，`XZ_CRON_ENABLED` 兼容 alias；`shutdown_scheduler()` 变 async + await `npc_loop_stop_all()`；`status_dict()` 加 `npc_loop_pool`
    - 修改 `backend/app/scheduler/__init__.py` — 导出 `NpcLoop`/`NpcLoopPool`/`GroupChatSemaphore`/`LettaRetryExhausted`/`stream_via_letta_with_retry`
    - 修改 `backend/app/routers/admin_cron.py` — `TriggerRequest.service` 加 `"npc_loop"` 字面量 + `target` (role_key)；`POST /api/cron/trigger` 新分支调 `pool.trigger_one(target)`
    - 修改 `backend/app/memory/agent_memory.py` — 新增 `load_recent_group_events(*, limit=20) → list[AgentMemoryEntry]` (ORDER BY timestamp DESC, source='group' 硬过滤)
    - 修改 `backend/tests/test_scheduler.py` — autouse fixture 加 `set_npc_loop_pool(None)`；2 处 `shutdown_scheduler()` 改 `await`（之前是 sync，shutdown_scheduler 现在 async 因为要 await npc_loop_stop_all）
    - 修改 `.env.example` — 新增 `GC_LOOPS_ENABLED` 段（默认 true）+ `XZ_CRON_ENABLED` 兼容 alias 文档
    - 新 `docs/decisions/0007-npc-self-driven.md` — ADR-0007（Y-Statement / 3 选项 / 决策 = B / Option B scope / Acceptance criteria 7 条 / Risks）
    - 新 `backend/tests/test_npc_loop.py` — 21 个测试覆盖 7 条 ADR acceptance criteria + 14 支持测试（21/21 PASS in 25.45s）
- **Eval gate**：pytest **66/66 PASS** (49.96s) — 含 21 个新增 npc_loop 测试 + 45 旧测试；ruff clean on 9 个新/改文件；预先存在的 `app/llm.py:19` Pydantic V1 + `app/graph.py:847` asyncio deprecation warning 跨多 commit 不在本 scope
- 旧 evaluator false-positive 踩坑 (在重试时捕获)：`fresh_pool` fixture 之前做 `pool._semaphore_override = sem` 不起效（`get_group_semaphore()` 走 module-level singleton），导致 `test_loop_respects_group_semaphore` 在看似 random `is_in_cooldown()` 返回 False 时失败。修复：在 fixture 加 `set_group_semaphore(sem)` 让 module singleton = test sem
- 之前 timeout 复盘 (踩坑笔记 见 `memory/t5-npc-autonomous-loop-interrupted.md`)：2 次 15 分钟 branch cap kill；这次同一窗口内完成 commit
- **Stage 8 UI 美化 commit** `feat(stage8-ui)`：九洲一号群「深墨金」主题 — 前后端 + 启动器统一深色 + 金色 + 朱砂 + 远山青 4 色调色板
    - `frontend/app/globals.css` — 重写为深墨金 CSS 变量 (--color-bg #1F1F1F / --color-accent #C7A969 / --color-text #E8E1D4 / --color-accent-warn #8B3A3A / --color-accent-aux #5C7367) + 6 角色专属色 + 宣纸纹理 + 金光流文字工具类 (`.gold-text` / `.font-xiuzhen-title`)
    - `frontend/app/layout.tsx` — Google Fonts 加载 Noto Serif SC (400-900) + ZCOOL XiaoWei
    - `frontend/app/page.tsx` — 主入口保留 ChatRoom（深色版）
    - `frontend/tailwind.config.js` — 扩展 xz-* 九洲一号群色阶 + fontFamily.serif/xiaowei/sans + goldShimmer/inkReveal keyframes
    - `frontend/lib/ws.ts` — ROLE_META 6 角色色重新映射到主题色 (#C7A969 / #5C7367 / #8B3A3A / #6A8AAD / #B8B0A2 / #B07AB0)
    - `frontend/components/ChatRoom.tsx` — 顶栏「金箔九 logo」+ 金光流标题 + 金线按钮 + 深墨金 empty state
    - `frontend/components/ChatBubble.tsx` — AI 气泡深墨底 + 金线边框 + 角色色左条 + @mention 金底朱砂字
    - `frontend/components/ChatInput.tsx` — 金色输入框 + 金色「发送」按钮
    - `frontend/components/ContactList.tsx` — 80px QQ 风格窄列 + 远山青在线点 + 朱砂未读红点
    - `frontend/components/GroupSidebar.tsx` — 6 角色卡片（每角色独立渐变背景）+ 金光流群名
    - `frontend/components/DMWindow.tsx` — 角色色 header + 「独立记忆」远山青徽章 + 朱砂错误卡
    - `frontend/components/AgentAvatar.tsx` — 渐变头像（深色背景适配）+ 金字境界小标签
    - `frontend/components/TimeGroupDivider.tsx` — 金线分隔 + 金字时间标
    - `frontend/components/StreamingText.tsx` — 金色光标
    - `frontend/components/ConnectionStatus.tsx` — 远山青连接点 + 金色重连中 + 朱砂重连按钮
    - `frontend/scripts/screenshot_stage8_beautify.cjs` — 截图脚本（5 张真 PNG: main / chat / member / dark / dm）
    - `desktop-launcher/src/App.tsx` — getCurrentWindow 浏览器兼容（try-catch）+ 全屏 bg-ink-900
    - `desktop-launcher/src/Splash.tsx` — 金色「九」logo + 金光流「九洲一号群」标题
    - `desktop-launcher/src/GroupSidebar.tsx` — 金色边框 + 金光点 + 金色「当前活跃」卡
    - `desktop-launcher/src/MemberList.tsx` — 金色边框 + 远山青在线点
    - `desktop-launcher/src/Avatar.tsx` — 角色色渐变 + 金字境界小标签
    - `desktop-launcher/src/ChatIframe.tsx` — 金色 spinner + 朱砂错误
    - `desktop-launcher/src/ConnectionIndicator.tsx` — 远山青连接点 + 金色重连中
    - `desktop-launcher/src/roles.ts` — 同步 frontend ROLE_META 新色
    - `desktop-launcher/tailwind.config.js` — ink-* (1F1F1F / 1A1814 / 2A2620 / 342F28) + gold-* (#C7A969 / #D4B574 / #E0C58A) + cinnabar / jade
    - `desktop-launcher/src/index.css` — 深墨金 body 渐变 + 仙侠字体 + 金光流
    - `desktop-launcher/index.html` — Google Fonts preconnect + Noto Serif SC + ZCOOL XiaoWei
- **Eval gate**: tsc 干净 (frontend + launcher) + vite build PASS (170.19 kB js / 22.55 kB css) + 7 张真 PNG 截图 (docs/screenshots/stage8/B-beautify-*.png)

- **Stage 8 cron 主动行为调度 commit** `feat(cron)`：九洲一号群 NPC 主动发言 + DM 主动私信
    - `backend/app/scheduler/__init__.py` — package exports
    - `backend/app/scheduler/connection_registry.py` — process-wide `ConnectionRegistry` (active WS session → WebSocket handle), used by cron services to push events to live clients
    - `backend/app/scheduler/state.py` — shared `CronState` (enabled flag, npc_filter, fire_count, last_fire_at dict for 1h same-NPC throttle, last_dm_followup_at dict for same-pair throttle)
    - `backend/app/scheduler/xiuzhen_cron.py` — `XiuzhenCronService` with APScheduler `AsyncIOScheduler` + `IntervalTrigger`; fires every 5 min (env `XZ_CRON_INTERVAL_MIN`, clamped 1..1440); random-picks 1 of 6 九洲一号群 NPC; drives that NPC through `_stream_via_letta(role_key=npc_key, session_id=f"cron-{npc_key}", all_msgs=[SystemMessage(persona), HumanMessage("[system] 你想跟群里说点啥？")])`; fan-out reply to all 6 NPC memories via `AgentMemoryStore.fan_out_group_event`; pushes `cron_agent_post` event to all active WS sessions; graceful degrade on any exception
    - `backend/app/scheduler/dm_followup.py` — `DmFollowupService` with `IntervalTrigger`; fires every 1h (env `XZ_DM_FOLLOWUP_INTERVAL_HOUR`); scans `AgentMemoryStore` for `(session_id, agent_key)` pairs whose latest entry > 24h ago (env `XZ_DM_FOLLOWUP_IDLE_HOUR`) AND has at least one `dm` source user entry; calls `_stream_via_letta` with `[system] 你已经很久没主动联系这位道了…` prompt; persists agent reply back to AgentMemoryStore (source=dm)
    - `backend/app/scheduler/lifespan.py` — `start_scheduler()` / `shutdown_scheduler()` + `SchedulerBundle` (idempotent)
    - `backend/app/routers/admin_cron.py` — `GET /api/cron/status` (snapshot: started + xiuzhen{running, interval_min, enabled, npc_filter, next_fire_time, fire_count, last_error} + dm_followup{...}); `POST /api/cron/toggle` (live: enabled / npc_filter / interval_min / interval_hour / idle_hour, all optional); `POST /api/cron/trigger` (manual force-fire, for ops/smoke)
    - `backend/app/main.py` lifespan 接入 `start_scheduler()` / `shutdown_scheduler()`，失败 graceful 不阻塞 BFF 启动
    - `backend/app/routers/ws.py` — WS accept 时 `get_connection_registry().register(session_id, websocket)`；finally 块 `unregister(session_id)` (覆盖正常断开 + 异常退出)
    - `backend/tests/test_scheduler.py` — 13 个 pytest 覆盖：lifespan 启停 scheduler / trigger_now 调 `_stream_via_letta` / fire_count 自增 / 1h 同 NPC 节流 / 100 次采样 6 NPC 均匀 / disabled 全局跳过 / 空 active session 优雅 / DM idle 扫描 / DM fire 持久化 / admin toggle+status endpoint (via httpx ASGI transport) / env clamp / ConnectionRegistry 基本操作 / active WS push
    - `backend/pyproject.toml` 加 `apscheduler>=3.10.0` 依赖
    - `backend/README.md` 加「Cron 主动行为调度」段 (env 变量 / admin 端点 / 失败兜底说明)
- **Eval gate**：pytest 45/45 PASS (13 新 + 32 旧, 14.07s) + ruff check clean (新文件无 lint) + 80% coverage on scheduler 包 + live `/api/cron/status` HTTP 200 + JSON

> 历史：Stage 6 DM Phase 2（双 commit）已 commit 并 verifier-audit PASS（4f69676 / 65634a2）。

## 关键决策（不要重做）

1. **LangGraph 而不是 CrewAI**（可控 + Checkpoint + 工程化）
2. **6 个小说角色固定**：宋书航 / 药师 / 狂刀三浪 / 北河散人 / 白前辈 / 灵蝶尊者
3. **WebSocket 自定义协议**（不是 SSE）：消息类型 = `user_msg` / `agent_thinking` / `agent_msg_chunk` / `agent_done` / `vote` / `summary` / `interrupt`
4. **调度策略**：LLM 批量提取六角色语义意图；确定性规则决定 0～2 位发言者。用户明确 @ 有选择兜底
5. **Next.js 15 App Router**（最新）
6. **InMemorySaver（MVP）→ PostgresSaver（生产）**：先简单，必要时升级
7. **流式输出用 LangGraph `stream_mode="custom"`**：而不是 `messages`

## 踩坑笔记（Generator 维护，每完成一个任务就更新）

### 2026-07-09 T10 Cleanup 包（双 commit `f456033` + `644f47e`）

修真群目录清理 + sidebar hover 2px 删除。踩坑如下：

- **策略选择：git rm vs mavis-trash — 按"是否在 git 历史里"分流**
  - 用户原话"用 git rm 保留历史"暗示他们知道这些文件 commit 过。
  - 但实际上, 修真群 ~50 个目标文件**只有 25 个真在 git 里**:
    - Tracked 25 个 (commit 过的中间产物) → `git rm` (保历史, commit 里能 git checkout 回来)
    - Untracked 41 个 (历史 .log / scratch 临时文件) → `mavis-trash` (无历史, 直接回收站, 可恢复)
  - 教训: **别盲信用户的"git rm"指令** — 先 `git ls-files` 验证哪些真 tracked, 否则 `git rm <untracked>` 会 fatal error。混用 git rm + mavis-trash 是 user intent 兼顾实际工具语义的折中。
  - 用户 spec 说"~45 个" — 真数 25 tracked + 41 untracked = 66 总数。差大是因为 spec 假设所有目标都 commit 过, 但修真群 .log 一直走 *.log 通配符 gitignore, 几乎从未入仓。

- **Piece A — `transition-all` vs `transition-shadow` 选哪个？**
  - 原行: `transition-all hover:-translate-y-0.5 hover:shadow-lg hover:border-[#C7A969]/40 hover:shadow-[#C7A969]/10`
  - 用户要求"删除 hover 上浮位移 (2px) 但保留其他 hover 视觉效果"。
  - 我一开始想直接 `hover:-translate-y-0.5` 删了, 保留 `transition-all`。
  - 但 `transition-all` 在没有 transform / translate 时是浪费 (background / color 已经各自走 transition)。
  - 改成 `transition-shadow` — 只过渡 shadow, 匹配 hover:shadow-* 的精确意图, 更轻。
  - 教训: Tailwind 的 `transition-all` 是"全过渡"模糊语义, 实际只需要过渡某几个属性时应该用 `transition-{property}` 精确版, 既性能好又表达清晰。

- **Piece B — `mavis-trash` 对 .log files with leading dot 的处理**
  - Windows PowerShell 传 `.uvicorn_cron.log` 这种 leading-dot 文件名, `mavis-trash` 接收正常, 41 个文件全 trash 成功。
  - **但** 第一次写 script 时我担心 leading dot 在 PowerShell 里会被当 switch 解析 — 没有, mavis-trash 内部走 Node, 参数语义跟 PS 解耦, 没事。
  - 教训: Windows 下的 dotfile (`*.log` 以 `.` 开头) 走 mavis-trash 安全, 不需要 escape。

- **Piece B — `git rm` 顺序: 单次 batch 比逐文件更安全**
  - 我用 `git rm -r <25 个文件>` 一次性 batch, git 自动按字母序处理并报 26 个 staged delete (含 .gitignore modify)。
  - 失败时 (如果某文件不存在) git 不会继续 (single `git rm` 命令是原子的, error 退出前已 stage 的仍然可处理)。
  - 教训: 删一组文件, 单次 `git rm -r` 比 25 个 `git rm` 单独跑好 — 出错中断时不会半半拉拉留下"半 staged"。

- **Piece C — `.gitignore` 加 `*.log` 已经够, 为何还要 explicit patterns？**
  - 修真群 `.gitignore` 第 32 行已经有 `*.log` 兜底 — 理论上 `backend/.uvicorn_cron.log` 应该自动被忽略。
  - 但用户 spec 明确要求 explicit patterns, 我**没**argue 这个"redundancy"。
  - 理由可能: (a) 兜底规则在某些 git 版本 (老 git < 2.0?) 处理 leading-dot 文件有 edge case; (b) explicit patterns 让 reviewer 一眼能看出"修真群约定不 commit 哪些 log"; (c) 给未来 maintainer 信号: 这些是修真群特有的 log family, 别 reopen。
  - 教训: 用户 spec 写 explicit patterns 时不要 argue "已经 *.log 了" — 可能用户有 reviewer-experience 的考虑。

- **Eval gate 跑法: pytest 数量从 92/93 → 98/98**
  - 我**没**碰任何 test_*.py, 怎么 pytest 数从 92 涨到 98?
  - 原因: T9 commit `628a882` 的 `test_history_delete.py` 18 个 + `test_agent_memory.py` 4 个 + `test_dm.py` 1 个 = 23 个新测试 (上次的 92 包含), 加上其他 (例: letta integration / DM store isolation) = 98。
  - 我删的 2 个 probe_*.py 是 standalone script, 不被 pytest 收集。
  - 跑 98/98 全过 (无 fail) = 0 regression. 这次的 0 fail 跟 T9 的 92/93 1 flake 不冲突 (那 flake 是 `test_ws_dm_full_flow` 偶发 isolation re-run PASS in 4.99s — 跟 cleanup 无关)。
  - 教训: 删 standalone 脚本不影响 pytest 数, 因为它们不是 test_*.py。

- **Untracked `.harness/` 下的子文件 — 我 session 自己的别误删**
  - 用户列了 4 个 root `.harness/` 文件要删: `lot2_audit_mvs_*.md` / `verifier_lot2_payload.md` / `stage8b-prd.md` / `stage8b_report_payload.txt`。
  - 但 root `.harness/` 还有 3 个我**自己** session 留下的: `verifier_T9_session_fix_audit.md` + `frontend-snapshot-20260709-022407.tar.gz` + `stage8b-plan.yaml`。
  - 我**没**碰后 3 个 (它们没在用户 spec 里)。
  - 教训: 用户 spec 列出文件时, **先看自己 session 在该 dir 留了什么**, 避免误删 verifier 自己或 sibling 留下的 audit material。

- **Git status 净度 = 真净度**
  - 删完 25 + trash 41 + 改 .gitignore + 改 GroupSidebar.tsx, `git status` 报 `nothing to commit, working tree clean`。
  - 之前担心 `mavis-trash` 留下 .nfs 或类似 locks — 没有, Windows 走回收站不动 inotify 等 unix 概念。
  - 教训: Windows + mavis-trash 流程比 unix + rm 简单 (no orphan inode worry)。

### 2026-07-09 Stage 8+ T9 session 体验修复包（三 commit `9bcf9c6` + `b8c1adb` + `628a882`）

修真群 Group Chat / DM 用户实测 3 个 UX 问题, 一并修完。踩坑如下：

- **bug A — module-level state 失效 → localStorage 救场 (Piece A)**
  - 现象：用户 hard reload 后, DM 历史变空, 必须重新发消息才能累积
  - 根因：`useDmSession.ts` 用 module-level `SESSION_ID_CACHE: Map<RoleKey, string>`
    跨刷新共享 sid。Map 在 (a) Fast Refresh / HMR rebuild (b) React StrictMode
    double-mount (c) 代码分割触发 module registry reset 三种场景下会**全部蒸发**
    → 下次 useDmSession mount 时 `getOrCreateDmSessionId()` 走 cache miss 分支
    → mint 新 sid → backend `dm_init` 用新 (sid, target) 查 memory → 返空 → UI 显示空
  - 修法：把 cache 从 module-level Map 换成 `localStorage` key
    `dm-session-id:<target>` (per-target 隔离, 避免 shu-hang 与 ling-die 互相覆盖).
    localStorage 在 hard reload / StrictMode / HMR / 跨 tab 都不蒸发。
  - `useEffect` 镜像 sessionId → localStorage 防止新 sid 后忘了持久化.
  - `reconnect()` 默认行为从"重建 + mint 新 sid"改成"重建 + 复用 sid" —
    WeChat-like UX。`{forceNewSession: true}` opt-in 给 Clear 按钮专用.
  - 教训：Module-level mutable state 在 React 19 / Next 15 + dev mode HMR 下极
    易失效. 任何"持久化"语义应该用 storage API (localStorage / IndexedDB /
    sessionStorage) 或者 React state, 不要用 module-level.

- **bug B — SQLite ALTER TABLE 不会自动 backfill + SQLite 没有 `IF NOT EXISTS COLUMN` (Piece B)**
  - 现象：现有 DB 加 `author TEXT` 列时, `CREATE TABLE IF NOT EXISTS` 不会改
    schema, 而 SQLite 没有 `ADD COLUMN IF NOT EXISTS` 语法
  - 修法：在 `init_schema()` 里跑一次 `_migrate_add_author_column` —
    - `PRAGMA table_info(agent_memory)` 探测列是否存在
    - 不存在 → `ALTER TABLE agent_memory ADD COLUMN author TEXT`
    - 存在 → 跳 ALTER
    - 接着 `UPDATE ... WHERE author IS NULL AND role = 'user'` 给旧 user rows
      backfill `'神秘人'`. 这样前端永远看不到 `null` author, 旧数据视觉一致
  - 对 agent rows 不 backfill (`role='agent' AND author IS NULL` 保持 NULL),
    因为 AI 不应该署 user 名
  - 教训：SQLite schema 演进要么用 `IF NOT EXISTS` (CREATE 层面支持, ALTER 不支持)
    要么自己探测 + ALTER. 别指望 ORM.

- **bug C — Pydantic Literal 字段在 `_verify_agent_blocks` 这类 ORM-style probe 之前会被 reject**
  - 教训：t9 commit 前 8 commits 里学到过；T9 没踩到，但 prompt 一下 verifier
    "如果把 author 字段做成 Literal['神秘人'] 之类的 enum, 会导致 ws.py payload
    验证失败" — 当前实现是 Optional[str] 让所有路径都 OK.

- **bug D — `try:` 这种 Python 语法写到 TS 文件里的低级错误**
  - DMWindow.handleClearDmHistory 我首次写时把 `try:` 写成了 Python 风格
    (因为我同时在 bash 里写 python 脚本, 脑子串线了). tsc 立刻报 `expected {`,
    改回 `try {`. 教训：同一 session 里切多个语言时, 每次用 edit/write tool
    都要 restart 一下思维 — 写 .ts 之前先想 "this is JS, not Python".

- **bug E — `app.routes` 不显示 include_router 进去的 routes**
  - 想验证 `history_delete.py` router 是否真注册到 FastAPI app,
    看了半天 `app.routes` 只剩 11 条 (/, /health, /docs 等基础). 实际
    OpenAPI schema (`app.openapi()`) 包含全部. 教训：用 `app.openapi()['paths']`
    做 round-trip 验证, 不要 enumerate `app.routes`.

- **bug F — Python 中 strict_length-descending order for mechanical replacement**
  - 没踩到 T9 范围；但 T9 里多个 file 改 string `SESSION_ID_CACHE` →
    `dmStorageKey` 时, 我用了手工精确替换 (oldString 精确匹配) 而不是
    bulk replace. 因为改的是局部逻辑, 不是 rename. 教训：当你**确实**做
    rename 时, 用 safe-refactor skill 提的长度-降序规则; 做局部修改时,
    Edit tool 精确 oldString 即可.

- **bug G — Async test 在 await asyncio.sleep() 时偶发 flake**
  - `test_ws_dm_full_flow` 用 `_fast_mock = MockChatModel(chunk_delay_ms=0)` 应该
    立刻推完所有 chunk. 但实际上 mock 内部仍有同步 block, 1.0s 等待窗口偶发
    来不及. 这是 Stage 6 DM Phase 2 commit `65634a2` 时代的 pre-existing flake,
    T9 commit 范围之外, 不修复 (isolation re-run PASS in 4.99s).

### 2026-07-03 Stage 7 P0 xiuzhen Letta 修复（commit `a1c7499`）

九洲一号群 Letta 集成 deliverable (`4f38e87`) 之后发现的两个 production issue：

- **bug A（用户实测）：model handle 错了**
  - 九洲一号群 agent 用 `minimax/MiniMax-M2.7-highspeed`，Letta 0.16.8 返
    HTTP 500；改用 `openai-proxy/MiniMax-M2.7-highspeed` 才能注册
  - Letta 的 OpenAI-compatible cloud 模型全部走 `openai-proxy/`
    namespace（`minimax/` 是 Letta 早期命名，0.16.x 已不识别）
  - 修复：`backend/app/config.py` `letta_llm_model` default 从
    `minimax/MiniMax-M2.7-highspeed` → `openai-proxy/MiniMax-M2.7-highspeed`
  - 验证：`curl /v1/agents/<id>` 返 `model: openai-proxy/MiniMax-M2.7-highspeed`
- **bug B：bootstrap 路径下 persona block 没写入**
  - 原 BFF lifespan bootstrap 创建 agent 后没保证 memory_blocks
    落地；6 NPC agent 全 `memory_blocks=[]`，LLM 走 generic AI 模板
  - 修复：写 `scripts/fixup_xiuzhen_npc_agents.py` — DELETE stale agent
    + 重新走 `get_or_create_agent_id`（带 `build_npc_memory_blocks` payload）
    + POST /v1/providers/ 注册 `minimax` provider（409 → 幂等）
    + 验证 GET /v1/agents/<id> blocks=4 且 persona 含 `九洲一号群`
- **defensive measures（不依赖 bug 修好）：**
  - `_stream_via_letta` 把 SystemMessage 内容直接拼进 user prompt 头部
    （`【系统设定】...`），即使 agent core-memory 空也保 persona 仍到 LLM
  - 加 `【回复要求】100-300字以内` 长度上限行，挡 qwen2.5:1.5b /
    M3 的"5 段论文"倾向
- **provider 行为：**
  - `minimax` provider 软删除会连带清空它下面的 10 个 model row；
    POST 同名 provider 可恢复（id 复用）。**生产环境不要乱 re-register**
  - 当前 live Letta：`minimax` provider (`api_key_enc` len=125) +
    6 NPC agents (openai-proxy/MiniMax-M2.7-highspeed + 4 memory_blocks
    + persona 九洲一号群) 均已正确
- **Eval gate 验证**：
  - pytest tests/ -q: **32/32 PASS**（28 旧 + 4 新 graph.py unit test）
  - ruff check backend/app config/graph + scripts + tests/test_graph.py: clean
  - live Letta curl verify: minimax provider yes, 6 NPC each blocks=4
    persona_has_marker=True, all model openai-proxy/... ✅
- commit：`a1c7499`（846 行 diff，5 文件：config.py / graph.py /
  scripts/{__init__,fixup_xiuzhen_npc_agents}.py / test_graph.py）

### 2026-07-03 Stage 7 P0 fixup script runtime bug（commit `2272d82`）

attempt 2 之后 verifier 反馈：fixup 脚本实际从未成功跑过（32/32 pytest 是真过，但 fixup script 自己有 3 个 bug 没被 pytest 覆盖到）：

- **bug 1：`_bootstrap_one` 把 httpx.AsyncClient 传给 `get_or_create_agent_id`**
  - `get_or_create_agent_id(client, ...)` 内部调 `client.create_agent()` + `client.get_agent()` —— 这两个是 `LettaClient` 的方法，httpx 没有
  - 跑 fixup 会 raise `AttributeError: 'AsyncClient' object has no attribute 'create_agent'`
  - 修复：`_bootstrap_one(http, letta_client, role_key)` 同时接两个 client；httpx 负责 `_list_npc_agents` / `_delete_agent` 等 LettaClient 没 wrap 的 ops；letta_client 传给 `get_or_create_agent_id` 和最终 verify 的 `get_agent`
- **bug 2：`_verify_agent_blocks` 只查 `full['memory_blocks']`（snake-case 顶层）**
  - Letta 0.16.8 实际返 `full['memory']['blocks']`（嵌套）→ 全 False
  - 修复：依次试 `memory.blocks` / `blocks` / `memory_blocks` 三种 shape
- **bug 3：provider POST body 多塞了 `provider_category=byok`**
  - Letta 0.16.8 schema 不接受这个字段 → 422 (`extra_forbidden`)
  - 修复：drop 它；Letta 看 body 有 `api_key` 自动 assign `provider_category=byok`
- **verifier false-negative 复盘**
  - verifier 用 `curl /v1/agents/?name=npc-*` 检查 → Letta 不支持 name glob，返回空列表
  - 正确查询：`curl /v1/agents/?limit=200`（无 name filter，列全部）或 `curl /v1/agents/?name=npc-shu-hang`（exact match）
  - **生产环境自查时记住**：Letta 0.16.8 name query 是 substring/exact match，不是 glob
- **live verification（修复后）**：
  - `python -m scripts.fixup_xiuzhen_npc_agents` → `RESULT: OK created=6/6 verified=6/6`
  - `PROVIDER minimax: registered (already present, 409 幂等)`
  - 6 NPC agents 全部 model=`openai-proxy/MiniMax-M2.7-highspeed`, blocks=4, persona_has_marker=True
- commit：`2272d82`（scripts/fixup_xiuzhen_npc_agents.py, +66/-17）

### 2026-07-03 Stage 7 Letta v0.16.8 集成（三 commit）

- **架构决策：复用 Project A 的 Letta server，不重起一个**
  - Project A 在 8283 起了一个 `pet-letta` 容器（27h+ healthy），已经在跑
  - 直接 `LETTA_BASE_URL=http://127.0.0.1:8283` 指向同一个 server，避免 port 冲突
  - 6 个 NPC agent 通过 `agent_name_for(role_key)` 命名（`npc-shu-hang` / `npc-yao-shi` / ...），不跟 Project A 的 agent 命名冲突
  - 优势：节省 Docker 资源、验证 Letta leaf 真在生产跑、不用维护两套 postgres
- **Ollama handle 而不是 OPENAI key**
  - `.env`：`LETTA_LLM_MODEL=local-ollama/qwen2.5:1.5b`
  - 选 1.5b 是因为它在 pet-ollama 已经 pull 过了，且体积小 bootstrap 快
  - 后续如果切到 GPT-4o-mini，只需改 env + restart（不重新 commit 代码）
- **graceful degrade 是硬约束**
  - `_use_letta_path()` predicate 永不覆盖 mock（USE_MOCK_LLM=true 永远走 mock，方便 dev / test）
  - `_stream_via_letta` 抛任何异常都 fallback 到 `get_chat_model(provider=...)` legacy 路径
  - fallback 时 emit `letta_fallback` / `dm_error{code: LETTA_FALLBACK}` 事件给前端展示
  - 这样 docker compose letta 挂了 / 网络不通 / agent 404 / SSE 解析失败都不会让 BFF 整体挂
- **idempotent bootstrap（重要！）**
  - 每次 BFF 启动都会调 `bootstrap_all`，但不会重复创建 agent
  - `get_or_create_agent_id` 走 cache → DB row → verify-alive → 找不到再创建
  - verify-alive 用 GET /v1/agents/{id}，404 → 重建 + update registry row（"recovered" outcome）
  - 启动 1 次会创建 6 个；启动 100 次还是 6 个，created/reused 比例会变化而已
- **为什么不用 `letta-client` SDK**
  - SDK pins 特定 httpx/letta 版本组合，跟我们 pinned v0.16.8 server 有 drift
  - Stage 7 只用 5 个 endpoint（`/v1/health` + `/v1/agents` CRUD + `/messages/stream`），手写 wrapper 单点可控
  - 后续如果 Letta API 漂移只改一个文件
- **probe 4/6 PASS — DM 路径 graceful degrade 触发**
  - 步骤 [1] health / [2] session_init / [3] group via Letta leaf / [6] dm_init#2 persistence — 全过
  - 步骤 [4] dm_init 偶尔返空错误，步骤 [5] dm_msg 触发 `LETTA_FALLBACK` 走 legacy per-role provider — graceful degrade 验证通过
  - 根因待查（疑似 SSE 解析 / 空错误 message），**不影响功能**：DM 还能用，只是走 legacy provider 而不是 Letta
  - 后续 sprint：debug `_stream_via_letta` 在 DM 上下文 raise 的异常（empty exception repr 暗示 generator 内 `yield` 后的 hidden issue）
- **probe 架构决策：Phase A (group) + Phase B (DM) WS 重连**
  - 跟 Stage 6 DM Phase 2 的"DM ↔ 群聊切换需重连"对齐
  - 同一个 session_id (URL) 让 AgentMemoryStore 跨重连保留 (session_id, agent_key) 历史
  - commit `d7ec152`
- **uvicorn 必须 restart 才能加载新 lifespan hook**
  - commit `5fdfcc8` 后启动的 uvicorn PID 40508 (2026-07-03 07:05) 加载了 Letta bootstrap
  - 当前 /api/health 返 `letta.status: up` + 6 NPC agent_id 都列出来了
  - restart helper: `restart_uvicorn.ps1`（自动 poll /health 等到 200）
- **三 commit split 的理由**
  - (a) `e19f3eb` self-contained bridge module + 5/6 tests pass — verifier 可以独立 audit 模块本身
  - (b) `5fdfcc8` wire into app — graph.py + main.py + config + compose + scripts
  - (c) `d7ec152` probe fix — probe-only change，不动 backend 行为

### 2026-07-02 Stage 6 DM Phase 2（本次 commit）

- **架构决策：DM 与群聊同 WS 连接，互斥**
  - 客户端发 `dm_init` → 进入 DM 模式直到断连
  - DM 模式下发 `user_msg` → 服务端 reject (`MODE_CONFLICT`)，保底不让群聊消息污染 DM 流
  - 同 session_id 内 DM ↔ 群聊切换需重连，避免 state machine 复杂化
- **隐私保证：SQLite WHERE 子句硬隔离**
  - DmStore.load_history(session_id, agent_key) 严格按 (session_id, agent_key) 过滤
  - 即使调用方传错 agent_key，也只会返回空列表（不会跨 agent 泄漏）
  - e2e probe 5/5 + pytest test_dm_store_isolation 覆盖
- **provider routing：DM 走单 agent 的 provider 配置**
  - stream_dm_chat 读 `ROLES[target_agent_key]["provider"]`，与群聊一致
  - 这样 DM 角色也可以走专属 LLM（比如白前辈走 Agnes 而非默认 M3）
- **持久化顺序：先存 user → 加载历史（含刚存的 user）→ stream → 存 agent full_text**
  - stream_dm_chat 收到的 history 不含刚 append 的 user（`history[:-1]`），自己内部追加
  - 这样失败时 DB 至少保留 user 消息（不算 lost）
- **测试设计：pytest 风格 + standalone 入口都支持**
  - 10 个 test 函数（同步 3 个 + 异步 7 个），pytest 全部收集
  - `if __name__ == "__main__` 入口用 `_StandaloneStore` / `_StandaloneMonkey` 简化跑（仅 smoke 子集）
- **重启旧 uvicorn 才生效**：stage 6 phase 1 commit 后启动的 uvicorn 进程（PID 35612）不认识 dm_init
  - 必须 Stop-Process + Start-Process 重启，e2e probe 才能 5/5
  - 后续迭代时注意：每次大改 ws.py / graph.py 都要重启
- **mock LLM 验证够了**：USE_MOCK_LLM=true 跑出 61 chunks，跟真实 LLM 走的是同一段 stream_dm_chat 逻辑
  - 上线前必须再用真实 LLM 跑一次 smoke（前端-expert 接好后一起验）

### 2026-07-02 Stage 6 DM Phase 2 frontend（本次 commit 续）

- **架构决策：DM 用独立 ChatSocket + 独立 sessionId**
  - backend 注释 "切换模式需重连 WS" 暗示共用 socket 复杂化；前端开第二条 socket 更稳
  - sessionId 形式 `dm-{target}-{newId()}`，不同 target 之间不串
  - **跨 mount 持久化**：模块级 `SESSION_ID_CACHE: Map<RoleKey, string>` 缓存 target→sessionId
    映射，同 tab 切群聊再切回 DM 时复用同一 sessionId → backend 端 (session_id, target_agent) 历史能加载
  - 跨 page refresh 走新 ID（module reload 即失效），合理
- **memorySize 累加逻辑：send() +1 / dm_done +1（不是 +2）**
  - 后端每轮 +1 (user) +1 (agent) = +2
  - 前端 send() 已经 optimistic +1 (user echo)，dm_done 再 +1 (agent 持久化) = +2 显示
  - 早期误写 dm_done +2 → 显示比真实高 1，已修
- **dm_thinking 兜底**：如果 backend 把 dm_thinking 和 dm_msg_chunk 合并发，或网络顺序乱了，
  第一个 chunk 没有对应的 streamingIdRef → 自己造一个 bubble 显示（不让 agent text 静默丢失）
- **Hydration #418 修复（dev mode 可见，production 干净）**：
  1. `Date.now()` 在 footer 时间显示 → 改 `useEffect` 异步 + `suppressHydrationWarning`
  2. `sessionId`（useChat 随机生成）渲染在 RoomHeader `<p>` → 加 `suppressHydrationWarning`
- **React StrictMode dev 警告**：dev mode 下 effect double-invoke 会触发 "WebSocket closed before
  connection established" warning。production 模式（`next start`）干净通过。verifier 看 prod build 即可。
- **Smoke test 设计**（5/5 PASS）：
  1. group chat baseline（contact list 渲染）
  2. 点白前辈 → DM 握手完成（记忆 0 条）
  3. 输入"凡人修仙..."→ 流式渲染 → 等 streaming 标签消失 + memorySize ≥ 2 → 截图
  4. back-to-group → 再点白前辈 → memorySize ≥ 2（持久化）
  5. back-to-group → 点药师 → memorySize = 0（跨 agent 隔离）
  - 退出码 0 = PASS
- **截图策略**：1280×720 桌面（agent profile 最小验证尺寸），5 张存 `frontend/docs/screenshots/stage6/`
- **dm_interrupt UX**：当前 backend 只 ack 不真打断；前端没接"中断"按钮（等 LangGraph aiter cancel 落地后再说）

### 2026-06-29 中断上下文（Planner 记录）

- 2026-06-29 P0 补完：B 组 Generator 完成 `backend/app/routers/ws.py`（关键 bug 修复）
  - 实现 `/ws/{session_id}` 端点：session_init / user_msg (含流式转发) / ping / interrupt
  - `import` 验证通过，py_compile 通过
  - P0 G0-1 / G0-3 静态验证通过（不跑 uvicorn，按指示不验证运行时）
  - 上次 Generator B1 写了 80% 后端代码但没 commit 就中断
- 仓库已有（**未 commit**）：
  - `backend/app/main.py`（53 行）
  - `backend/app/graph.py`（89 行，LangGraph + stream_mode="custom"）
  - `backend/app/llm.py`（91 行，5 个 provider + Mock 兜底）
  - `backend/app/models.py`（57 行，Pydantic WebSocket 协议）
  - `backend/app/config.py`（67 行，pydantic_settings + 无硬编码 key）
  - `backend/app/routers/__init__.py`（空）
  - `docker-compose.yml`（backend + frontend 框架）
  - `.env.example`（35 行完整环境变量）
  - `backend/.venv/`（依赖装齐：fastapi / uvicorn / langgraph / langchain / pydantic / pytest 等）
- **关键 bug**：`main.py` 引用了 `app.routers.ws`，但 `backend/app/routers/ws.py` **没创建** —— 当前无法启动
- **完全缺失**：`pyproject.toml` / `Dockerfile` / `frontend/` / `tests/` / `README.md` / Git commit
- 下次 Generator 任务：先检查 `routers/ws.py` 是否存在 → 不存在就**先写**（否则其他都是空谈）→ 然后补齐缺失文件 + 写前端 + commit

### 2026-06-29 P0 bug 修复（Verifier B 审计 → Generator 修复）

- **Bug 1（高）：MockChatModel yield 的 AIMessageChunk 缺 `.message` 属性**
  - 现象：LangGraph `astream` wrapper 内部读 `chunk.message.id`，纯 `AIMessageChunk(content=, id=)` 会抛 `AttributeError`，G0-5 流式输出必挂
  - 修复：yield 时同时构造 `message=AIMessage(content=ch, id=f"mock-{i}")`，chunk 与 message 共享 id/content
  - commit：`2a655fd`
  - 验证：`MockChatModel()._astream([HumanMessage(...)])` yield 120 chunk 全部带 `message=` 属性，LangGraph `astream` 跑通
- **Bug 2（高）：Dockerfile install 顺序——`app/` 还没 COPY 就跑 `uv pip install .`**
  - 现象：hatchling build wheel 时需要 `app/` 目录存在，但 Dockerfile 先 `COPY pyproject.toml` 就 `RUN uv pip install --system .`，自指 install 会失败
  - 修复：加 `--no-deps` 跳过 self-install，只装 deps。源码 COPY 保持原位
  - commit：`a74a12a`
- **Bug 3（中）：pyproject.toml 引用不存在的 `langgraph-checkpoint>=2.0.0`**
  - 现象：PyPI 上没有这个独立包，checkpoint 模块随 `langgraph` 主包安装；引用此包会让 `uv pip install` 解析失败
  - 修复：删除该行
  - commit：`5a64790`

## 现状（部分完成）

- 后端群聊：✅（Stage 1-5 九洲一号群 6 角色 + provider routing）
- 后端 DM 后端实装：✅（Stage 6 DM Phase 2，已 commit）
- 后端桌面启动器：✅（Stage 7 九洲一号群 Tauri）
- **前端 DM 联调：✅（Stage 6 DM Phase 2 frontend，本次 commit）**
- 文档：PROJECT_B_AUDIT / RE_VERIFICATION 已写（Stage 7）+ `frontend/STAGE6_DM_FRONTEND_REPORT.md`（本次）

## Eval gate 状态

### Stage 7 P0 xiuzhen Letta 修复（commits `a1c7499` + `2272d82`）

**Eval gate（PASS 集合）**：
- ✅ pytest tests/ -q: **32/32 PASS** (28 旧 + 4 新 graph.py unit test) in 6.58s
- ✅ ruff check on `backend/app/{config,graph}.py` + `scripts/` + `tests/test_graph.py`: clean (pre-existing `app/llm.py` F401 已记在"已知 lint 旧债"里)
- ✅ **Live runtime verification**：`python -m scripts.fixup_xiuzhen_npc_agents` end-to-end 成功，输出 `RESULT: OK created=6/6 verified=6/6`，6 个 NPC agent 全部重新 bootstrap
- ✅ Live Letta /v1/providers/ 返：`minimax` provider 已注册（`api_key_enc` len=125，`base_url=https://api.minimaxi.com/v1`，`provider_type=openai`，`provider_category=byok` — 由 Letta auto-assigned）
- ✅ Live Letta 6 NPC (`npc-shu-hang` / `npc-yao-shi` / `npc-san-lang` / `npc-bei-he` / `npc-bai-qianbei` / `npc-ling-die`)：
  - model=`openai-proxy/MiniMax-M2.7-highspeed`（handle 修正后）
  - `memory.blocks` 长度 = 4（persona / human / preferences / relationships）
  - persona block value 含 `九洲一号群` 标记（九洲一号群 marker）
  - 验证方式：`curl /v1/agents/?limit=200` 后过滤 `name.startswith('npc-')`，**不要**用 `?name=npc-*`（Letta 0.16.8 不支持 glob，会返空 list）
- ✅ `_stream_via_letta` 防御性 prompt 拼装：SystemMessage 拼到 `【系统设定】` 前缀、长度上限 `【回复要求】100-300字以内`
- ✅ `scripts/fixup_xiuzhen_npc_agents.py`：`fixup_all()` 函数 importable；CLI `python -m scripts.fixup_xiuzhen_npc_agents` 入口就绪，已 end-to-end 跑通（幂等 — 已存在 provider 返 409 视为 ok）

**Eval gate（非阻塞 note）**：
- ⚠️ provider re-registration 软删除会清空下面 10 个 model row — 生产环境不要随便 DELETE provider
- ⚠️ `?name=glob*` 查询 Letta 0.16.8 不支持 glob；用 `?name=exact` 或 omit name + `?limit=N`

### Stage 7 Letta v0.16.8 集成（三 commit）

**Eval gate（PASS 集合）**：
- ✅ pytest 28/28 PASS in 6.51s（含 6 个新增 Letta integration tests + 22 个 DM/AgentMemory 回归）
- ✅ ruff check `letta_bridge/` + `tests/test_letta_integration.py` + `tests/probe_letta_e2e.py` + 修改过的 app 文件：clean（仅 2 个已知 pre-existing warning：llm.py Pydantic V2 / graph.py asyncio deprecations）
- ✅ /api/health 返 `{"status":"ok","use_letta":true,"letta":{"status":"up","base_url":"http://127.0.0.1:8283","agents":[6 NPC...]}}`
- ✅ 6 NPC agents 在 BFF lifespan bootstrap 创建 + 持久化到 `backend/data/letta_npc_registry.sqlite`
- ✅ group chat via Letta leaf e2e 跑通（步骤 [3] 4 chunks from san-lang / bei-he / ling-die / bai-qianbei）
- ✅ dm_init#2 持久化生效（memory_size=8 → group fan-out 6 + 1 user msg + 1 user echo）
- ✅ DM 路径 graceful degrade：`dm_error{code: "LETTA_FALLBACK"}` 事件触发后 fallback 到 per-role provider，DM 仍可用
- ✅ pytest test_letta_integration.py 6/6 PASS (0.27s MockTransport)

**Eval gate（FAIL/non-blocking，DM-specific Letta path issue）**：
- ⚠️ probe step [4] dm_init 偶尔返空错误 message
- ⚠️ probe step [5] dm_msg 触发 `LETTA_FALLBACK`（`letta_exc` 是 empty string） — 走 legacy provider
- 群聊路径同样代码 `_stream_via_letta` 正常工作；DM 上下文疑似 SSE 解析或 generator 内 hidden issue
- **不阻塞合并**：功能降级到 legacy provider 而不是 broken；DM 仍能正常对话
- **不在本 commit scope**：后续 sprint debug

**已知 lint 旧债（不在本次 scope）**
- `app/llm.py:19` Pydantic V2 `class Config` 旧 syntax（pre-existing，跨多 commit）
- `app/graph.py:744` `asyncio.get_event_loop()` deprecation（pre-existing，跨多 commit）

### Stage 6 DM Phase 2（双 commit：后端 + 前端）

**后端（已 commit 早些时候）**：
- DM-1 `dm_init` 返回正确 schema (target/name/emoji/history/memory_size)：✅
- DM-2 `dm_msg` 流式回复（dm_thinking + ≥1 dm_msg_chunk + dm_done）：✅ (e2e probe 61 chunks)
- DM-3 持久化跨轮次生效（第 1 轮消息出现在第 2 轮 dm_init）：✅
- DM-4 跨 agent 隔离（yao-shi 看不到 shu-hang）：✅
- DM-5 DM 流不触发群聊 cycle（无 supervisor_decision / agent_* 事件）：✅
- pytest test_dm.py：10 passed / 10 (9.42s)
- ruff check (新文件 dm_store.py / models.py / routers/ws.py / test_dm.py / probe_dm_e2e.py)：All checks passed
- e2e probe (tests/probe_dm_e2e.py, ws://127.0.0.1:8000/ws/{sid})：5/5 全过
- /health：HTTP 200 `{"status":"ok"}`

**前端（本次 commit）**：
- DMF-1 DMWindow wire 到 useDmSession hook，握手 `dm_init` → history 渲染：✅
- DMF-2 输入 → `dm_msg` → 看到流式 chunk → dm_done 文本完整：✅
- DMF-3 跨刷新持久化（回群聊再进同一角色 → history 2 条可见）：✅
- DMF-4 跨 agent 隔离（白前辈 vs 药师互不可见）：✅
- DMF-5 失败处理（init.phase="failed" 显式错误卡 + 重连按钮）：✅（code path 实现 + 视觉验证 OK）
- DMF-6 next build：PASS（13.2 kB → 119 kB First Load JS）
- DMF-7 Playwright smoke（`scripts/stage6-dm-smoke.cjs`，1280×720）：**5/5 全过**
- DMF-8 Console errors (production build)：**0**
- 5 张 verify artifact 存 `frontend/docs/screenshots/stage6/`

### Stage 8+ T9 session 体验修复包（三 commit：`9bcf9c6` + `b8c1adb` + `628a882`）

**Piece A — DM sessionId localStorage 持久化（commit `9bcf9c6`）**：
- A-1 module-level `SESSION_ID_CACHE: Map` 被替换为 localStorage-backed persistence (`dm-session-id:<target>` key): ✅
- A-2 同一 target 跨刷新、跨 HMR、跨 StrictMode 双挂载: 同一 sid: ✅（useEffect 镜像 + useState initializer 读）
- A-3 不同 target 之间不串扰: ✅ (per-target key)
- A-4 `reconnect()` 默认仅 rebuild socket 复用 sid, `{forceNewSession: true}` opt-in 路径走新 sid: ✅
- A-5 新增 `clearDmSessionId(target)` export + hook 内 `clearLocalHistory()`: ✅ (Piece C 使用)

**Piece B — 「神秘人」默认 user ID + 可改（commit `b8c1adb`）**：
- B-1 新增 `frontend/lib/userIdentity.ts`：`getDisplayName()` / `setDisplayName()` / `useUserIdentity()` hook (含 cross-tab `storage` 事件同步): ✅
- B-2 默认 "神秘人"，trim + 24-char cap，empty/whitespace fallback 默认: ✅
- B-3 Backend `AgentMemoryEntry.author: Optional[str] = None` 字段: ✅
- B-4 SQLite schema 加 `author TEXT` 列 + ALTER-TABLE 迁移 (idempotent, PRAGMA table_info 探测), 旧 user rows backfill "神秘人": ✅
- B-5 `append_message()` / `fan_out_group_event()` 接受 author kwarg，user-typed 空 author 时 fallback "神秘人", AI-typed 行 author=None: ✅
- B-6 `load_agent_memory()` / `load_recent_group_events()` SELECT + 返 author 字段: ✅
- B-7 `ws.py` user_msg + dm_msg handler 读 `payload.author` (trim → fallback None → backend fallback): ✅
- B-8 `stream_group_chat()` author kwarg 透传到 `fan_out_group_event()`: ✅
- B-9 `group_history.py` 响应 entry dict 含 author 字段: ✅
- B-10 群聊 header inline 编辑器 (`UserBadge` 组件, "我: {name} ✎", Enter save / Esc cancel / blur save / 24 char cap): ✅
- B-11 DM header inline 编辑器 (`DmUserBadge` 组件, 同模式, 共享 localStorage): ✅
- B-12 `ChatBubble` user bubble 显示 "我：{author}": ✅
- B-13 历史恢复 (localStorage /api/group/history 重启后) 含 author 字段: ✅

**Piece C — 每窗口 Clear 按钮（commit `628a882`）**：
- C-1 `AgentMemoryStore.delete_session(*, session_id, agent_key=None, source=None)` 方法, 三种过滤维度可选组合: ✅
- C-2 `DELETE /api/group/history?session_id=<sid>` 只删 source='group' 行, 跨 6 角色同时清: ✅
- C-3 `DELETE /api/dm/history?session_id=<sid>&agent_key=<target?>` 删 DM 行, agent_key 可选: ✅
- C-4 cross-session isolation (A session DELETE 不影响 B session): ✅ (8 个 store 单元测 + 9 个 HTTP 集成测)
- C-5 empty session_id → 422 (FastAPI Query 校验): ✅
- C-6 群聊 header 朱砂红 "清除" 按钮 (`data-testid="clear-group-history"`) + confirm 防误触 + DELETE + `setMessages([])` + `reconnect({forceNewSession: true})`: ✅
- C-7 DM header 朱砂红 "清除" 按钮 (`data-testid="clear-dm-history"`) + DELETE 带 `agent_key=<target>` + `clearDmSessionId(target)` + `clearLocalHistory()`: ✅
- C-8 SQLite 真删验证 (live script 跑 3 group + 2 dm rows, 三阶段 DELETE 后 count 全部归零, 互不干扰): ✅

**Eval gate 总数**：
- ✅ pytest tests/ -q: **92/93 PASS** in 59.26s（18 个新 history_delete + 4 个新 agent_memory author + 1 个新 dm author end-to-end + 旧测试全过）
  - ⚠️ 1 pre-existing flake: `test_ws_dm_full_flow` — 该 test 用 mock LLM 但缺 `chunk_delay_ms=0`，stream `dm_done` 在 1.0s 等待窗口内偶发来不及推完。本 commit 范围之外，不修复（isolation re-run PASS in 4.99s）。
- ✅ ruff check `app/routers/history_delete.py` `app/memory/agent_memory.py` `app/main.py`: clean
- ✅ npx tsc --noEmit: clean
- ✅ npm run build: PASS (16.6 kB / 122 kB First Load JS)
- ✅ Live SQLite delete_session verify (3 阶段, group + dm 各 target 互不干扰)
- ✅ OpenAPI schema 含两个 DELETE endpoint

### 已知 lint 旧债（不在本次 scope）
- `app/config.py:4` `os` 旧 unused import
- `app/graph.py:32` `END` 旧 unused import
- `app/llm.py:12` `BaseMessage` 旧 unused import
- `tests/test_smoke_e2e.py` 旧 unused var + 假 f-string ×2
- `tests/test_stage5_trim.py` 旧假 f-string ×2
- 后续 sprint 处理（不影响本 commit）

## 代码规范

- Python：black + ruff
- TypeScript：eslint + prettier
- Commit message：**Conventional Commits**
- 测试：pytest（Python）+ playwright（E2E）
- 覆盖率：P0 ≥ 60%，P1/P2 ≥ 70%
- **关键：每个 PR 都要附 Eval gate 跑通截图/输出**

## 怎么推进（给 Generator 的工作流）

1. **读 AGENTS.md**（本文件）→ 了解上下文
2. **读 03_阶段规划.md 当前阶段** → 看任务清单 + Eval gate
3. **实现** → 写代码 + 跑测试
4. **验证 Eval gate** → 跑 `G0-1 ~ G0-8`，输出每条的 pass/fail
5. **更新 AGENTS.md** → 标记进度、记录踩坑、更新 Eval gate 状态
6. **commit** → 提交信息 + AGENTS.md 一起进 commit
7. **回 Planner** → "P0 完成，commit hash，G0-1~G0-8 全过" 或 "P0 失败，blocked on G0-X：[原因]"

## 上次更新

- 2026-07-09 04:55 — **T10 Cleanup 包完成**（2 atomic commits `f456033` + `644f47e`）
    - 用户要求清理项目目录"中间一次性脚本工具性的、文字性的"文件 + 删除 sidebar hover 2px 上浮
    - **Piece A — sidebar hover 2px 上浮删除（commit `f456033`）**:
      - `frontend/components/GroupSidebar.tsx:98` `transition-all hover:-translate-y-0.5 ...` → `transition-shadow hover:shadow-lg ...` (去掉位移,保留 border + shadow hover 效果)
    - **Piece B — git rm 25 中间文件（commit `644f47e`）**:
      - Backend scripts (1) `backend/scripts/fixup_xiuzhen_npc_agents.py`
      - Backend probe tests (2) `backend/tests/probe_dm_e2e.py` / `probe_letta_e2e.py`
      - Frontend scripts (7) `frontend/scripts/{bug3-layout-verify, screenshot, screenshot_stage5, screenshot_stage5_static, screenshot_stage8_beautify, screenshot_stage8b_beautify, stage6-dm-smoke}.cjs`
      - Backend STAGE/FIX/VERIFICATION/REPORT .md (9) `backend/{FIX_REPORT, FRONTEND_REPORT, MINIMAX_FIX_REPORT, RE_VERIFICATION, STAGE3_REPORT, STAGE4A_REPORT, STAGE4B_REPORT, STAGE5A_REPORT, VERIFICATION}.md`
      - Frontend STAGE*.md (2) `frontend/{STAGE5B_REPORT, STAGE6_DM_FRONTEND_REPORT}.md`
      - Desktop-launcher (1) `desktop-launcher/LAUNCHER_REPORT.md`
      - Internal reports (3) `backend/.harness/reports/{agent_memory_api, agent_memory_design}.md` + `frontend/.harness/reports/frontend_bugs_1_3_mvs_*.md`
    - **Piece B-extra — mavis-trash 41 untracked 文件**（无历史可保,直接回收站）:
      - Backend 17 .log (`.uvicorn_*.log` × 7 + `lot2_*.log` × 2 + `minimax_smoke_run.log` + `smoke_real_run.log` + `stage4a_*.log` × 2 + `uvicorn_audit_*.log` × 3 + `uvicorn_err.log` + `uvicorn_out.log`)
      - Backend 1 md (`backend/RE_VERIFICATION_DM_PHASE2.md` — gitignored via `RE_VERIFICATION_*.md` 模式,从未 commit)
      - Frontend 11 .log (`lot2_build.log` + `next_*.log` × 8 + `screenshot_run.log`)
      - Root 2 .log (`bff-restart.log` + `bff-restart2.log`)
      - Desktop-launcher 6 .log (`tauri-dev*.log` × 5 + `tauri-version-check.log`)
      - Root .harness/ 4 files (`plans/stage8b-prd.md` + `scratch/stage8b_report_payload.txt` + `reports/lot2_audit_mvs_*.md` + `reports/verifier_lot2_payload.md`)
    - **Piece C — `.gitignore` 新增 explicit log patterns**:
      - `*.log` 兜底（已有）+ `backend/.uvicorn_*.log` / `backend/uvicorn_*.log` / `backend/lot2_*.log` / `backend/stage*.log` / `backend/smoke_*.log` / `backend/minimax_*.log` / `frontend/next_*.log` / `frontend/lot2_*.log` / `frontend/screenshot_*.log` / `bff-restart*.log`
    - 验收：pytest **98/98 PASS** (44.00s, 0 fail) + tsc --noEmit clean + npm run build PASS (16.6 kB / 122 kB First Load JS, no regression)
    - tracked files 193 → 168 (exact 25 fewer, 与 git rm 数对得上)
    - 详见 「踩坑笔记」T10 节
- 2026-07-09 04:35 — **Stage 8+ T9 session 体验修复包完成**（三 commit atomic）
    - 修真群 Group Chat / DM 用户实测发现 3 个 UX 问题一次性修：
      1. **DM sessionId 跨刷新消失** — module-level `SESSION_ID_CACHE: Map` 在 Fast Refresh / HMR / StrictMode double-mount / 代码分割下失效，换 `localStorage`-backed persistence (key=`dm-session-id:<target>`, per-target 隔离)
      2. **用户没有 ID 显示** — user_msg 的 author 字段从无到全链路透传：frontend `userIdentity.ts` (默认 "神秘人") + inline ✎ 编辑器 (群聊 + DM header 都加) + SQLite `author TEXT` 列 + ALTER-TABLE 迁移 (旧 user rows backfill "神秘人") + `AgentMemoryEntry` model 字段 + `ws.py` user_msg/dm_msg payload `author` 读取 + `stream_group_chat` / `fan_out_group_event` 透传 + `group_history.py` 响应 + `ChatBubble` 渲染 "我：{author}"
      3. **每窗口没有清除按钮** — 后端 `DELETE /api/group/history?session_id=X` + `DELETE /api/dm/history?session_id=X&agent_key=Y` (新增 `history_delete.py` router + `AgentMemoryStore.delete_session` 方法) + 前端朱砂红 "清除" 按钮 (群聊 + DM 都加) + `confirm()` 防误触 + `clearLocalHistory()` / `clearDmSessionId()` hook + `reconnect({forceNewSession: true})` 强制新 sid
    - 三 commit split（每个独立 verifier-audit 友好）：
      - `9bcf9c6` Piece A — DM sessionId localStorage
      - `b8c1adb` Piece B — 「神秘人」author 全链路透传
      - `628a882` Piece C — Clear 按钮 + DELETE endpoints
    - 18 个新 `test_history_delete.py` 测试 + 4 个新 `test_agent_memory.py` author 测试 + 1 个新 `test_dm.py` author 端到端测试 = 92/93 PASS（1 pre-existing flake `test_ws_dm_full_flow` 跟 T9 无关 — 该 test 用 mock LLM, 缺 `chunk_delay_ms=0` 时 stream `dm_done` 在 1.0s 等待窗口内偶发来不及推完，是历史问题）
    - tsc clean + vite build PASS (16.6 kB / 122 kB First Load JS)
    - 详见 「踩坑笔记」T9 节
- 2026-07-04 20:25 — **Stage 8 cron 主动行为调度完成**
    - 九洲一号群 NPC 不再只被动回应用户 — APScheduler 驱动的两个 cron 服务在后台跑：
      1. **XiuzhenCronService** — 每 5 min (env `XZ_CRON_INTERVAL_MIN` 可调 1..1440) 随机选 1 个 NPC 走 `_stream_via_letta(forced_speaker=npc_key, user_msg="[system] 你想跟群里说点啥？")` 主动发一句；1h 内同 NPC 节流；fan-out 到九洲一号群 6 角色 memory；如果有活跃 WS session 推 `cron_agent_post` 事件
      2. **DmFollowupService** — 每 1h (env `XZ_DM_FOLLOWUP_INTERVAL_HOUR` 可调) 扫描 `AgentMemoryStore` 找 idle > 24h (`XZ_DM_FOLLOWUP_IDLE_HOUR` 可调) 且至少有过 DM user 消息的 `(session_id, npc_key)` 对；让 NPC 主动私信一句问候 (用 _stream_via_letta)；持久化回 AgentMemoryStore (source=dm)
    - 新模块：`app/scheduler/{__init__,connection_registry,state,xiuzhen_cron,dm_followup,lifespan}.py` (6 文件) + `app/routers/admin_cron.py` (3 端点) + `tests/test_scheduler.py` (13 个测试覆盖 lifespan 启停 / 单 NPC 触发 / 节流 / 6 NPC 均匀采样 / disabled 跳过 / empty session 优雅 / DM 24h idle 扫描 + 持久化 / admin endpoint / env clamp / active WS push / ConnectionRegistry 基本操作)
    - `main.py` lifespan 接入 `start_scheduler()` / `shutdown_scheduler()`；`routers/ws.py` 在 WS accept / disconnect 时注册到 ConnectionRegistry (process-wide singleton)
    - pytest **45/45 PASS** (13 新 + 32 旧, 14.07s) — 含完整覆盖率 80% on scheduler 包
    - ruff check clean (8 个原 F401/F841 已修)
    - live `curl /api/cron/status` 返 HTTP 200 + JSON `{started: true, xiuzhen: {running, interval_min: 5, enabled: true, npc_filter: null, next_fire_time, fire_count, last_error: null}, dm_followup: {...}}`
    - commit：`feat(cron): 九洲一号群 NPC 主动行为调度 — APScheduler XiuzhenCronService (5min) + DmFollowupService (1h idle) + ConnectionRegistry + admin /api/cron/{status,toggle,trigger}`
- 2026-07-04 20:30 — **Stage 8 UI 美化「深墨金」主题完成**
    - 九洲一号群 Next.js 前端 + desktop-launcher 统一深墨金 (background #1F1F1F + gold #C7A969 + 文字 #E8E1D4 + 朱砂 #8B3A3A + 远山青 #5C7367) + Noto Serif SC 衬线 + ZCOOL XiaoWei 书法
    - 九洲一号群 6 角色色按主题调色板重映射：宋书航 琥珀金 / 药师 远山青 / 狂刀三浪 朱砂 / 北河散人 玄青 / 白前辈 霜灰 / 灵蝶尊者 蝶粉紫
    - frontend 10 个文件重写（globals.css / layout.tsx / page.tsx / tailwind.config.js / lib/ws.ts / 8 components）+ desktop-launcher 8 个文件重写
    - tsc --noEmit 干净 (frontend + launcher 两端) + vite build PASS (170.19 kB js / 22.55 kB css)
    - 7 张真 PNG 截图存 `docs/screenshots/stage8/B-beautify-*.png` (main / chat-bubble / member-list / dark-mode / dm-window / launcher / launcher-splash)
    - commit (pending): `feat(stage8-ui): 九洲一号群「深墨金」主题 — Noto Serif SC + ZCOOL XiaoWei + #1F1F1F/#C7A969/#E8E1D4/#8B3A3A/#5C7367`
- 2026-07-03 13:10 — **Stage 7 Letta v0.16.8 集成完成（三 commit）**
    - commit (a) `e19f3eb` test(backend): Letta integration tests + bridge skeleton (6 tests)
    - commit (b) `5fdfcc8` feat(stage7-letta): wire Letta into BFF (graph.py + main.py + config + compose + scripts)
    - commit (c) `d7ec152` fix(tests): probe_letta_e2e splits Phase A (group) + Phase B (DM) WS per AGENTS.md §Stage 6 DM Phase 2 架构
    - pytest 28/28 PASS (6.51s)
    - 6 NPC agents 在 BFF lifespan bootstrap 创建（pet-letta:8283 healthy 27h+）
    - group chat via Letta leaf e2e 跑通（4 chunks）
    - DM 路径 graceful degrade 触发 `LETTA_FALLBACK` 走 legacy provider（不阻塞，DM 仍能用）
    - probe 4/6 PASS（步骤 [4] [5] 待 debug：DM-specific empty-exception issue，不在本 sprint scope）
- 2026-07-03 15:33 — **Stage 7 Letta v0.16.8 集成 ACCEPTED**（4 commits: e19f3eb / 5fdfcc8 / d7ec152 / 4f38e87）。verifier 报告 PASS + Planner 亲自 cross-check: pytest 28/28 真过 / SQLite 6 NPC 真持久化（shu-hang/yao-shi/san-lang/bei-he/bai-qianbei/ling-die）each row role_key → UUID + qwen2.5:1.5b / AGENTS.md Stage 7 段已记。**Eval gate B.P3.6 达成**（九洲一号群 Letta 化）。
- 2026-07-02 19:10 — **Stage 6 DM Phase 2 frontend 联调完成**（本次 commit 续）
    - 5/5 Playwright smoke pass (1280×720)：握手 / 流式 / 持久化 / 跨 agent 隔离 / 完成闭环
    - next build PASS（13.2 kB → 119 kB First Load JS，+3.9 kB vs Phase 1）
    - 5 张截图存 `frontend/docs/screenshots/stage6/`
    - 详细报告 `frontend/STAGE6_DM_FRONTEND_REPORT.md`
    - 待 commit：`feat(stage6-dm-frontend): DMWindow 接入后端 dm 协议 — useDmSession hook + 独立 ChatSocket + 流式渲染 + 跨刷新持久化`
- 2026-07-02 18:30 — **Stage 6 DM Phase 2 后端实装完成**（earlier commit）
    - 10 pytest passed / 10 (9.42s)
    - 5/5 e2e probe pass (61 chunks 流式, 持久化生效, 跨 agent 隔离)
    - ruff check 本次新文件 clean (8 个旧债留给后续 sprint)
    - commit：`feat(stage6-dm-phase2): DM 后端实装 — DmStore (SQLite) + stream_dm_chat + WS dm_init/dm_msg/dm_interrupt 分发 + 10 tests + e2e probe`
- 2026-06-29 03:45 — 创建（调研 + 规划阶段，commit `2bc57dc`）
