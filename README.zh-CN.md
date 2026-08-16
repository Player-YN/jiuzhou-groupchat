# 九州一号群 · Jiuzhou Group Chat

[English README](README.md) · [GitHub](https://github.com/Player-YN/jiuzhou-groupchat)

**持续群聊社交模拟：** 一个真人与六个固定小说角色长期共处同一群。角色开口或沉默，来自可评分的动机，而不是「把六个聊天机器人轮流叫起来」。

> 不是多 Agent 头脑风暴，也不是自动会议纪要。沉默是一等公民结果。

![群聊主界面](docs/screenshots/stage8/B-beautify-main-page.png)

## 为什么不是套壳聊天机器人

常见「六人同房」演示要么让 LLM 点名发言，要么全员轮询。本项目把 **语义** 和 **策略** 拆开：

| 层 | 谁 | 允许做什么 |
| --- | --- | --- |
| 特征提取 | 启发式规则（默认）或一次六角色批量 LLM | 输出 0–3 分项。**不得点出发言者。** |
| 策略 | 纯函数 `BehaviorEngine.decide()` | 硬门、加权分、阈值、0–2 人仲裁、允许全员沉默。 |
| 决策记忆 | SQLite `DecisionLogStore` | 只追加一次。同 `event_id` + 不同输入 → 冲突。 |
| 回放 | 同一引擎 + 日志里的原始输入 | 只重跑 **规则**，不重跑 LLM。字段必须一致。 |

明确 `@` 与私信 **从不** 等待 LLM 评估。普通群事件可选 **0 / 1 / 2** 人；空闲 tick **最多 1 人**；自主连锁最多 3 跳。

## 角色

| Key | 名字 | 声线（默认供应商） |
| --- | --- | --- |
| `shu-hang` | 宋书航 | 好奇主角 · MiniMax |
| `yao-shi` | 药师 | 惜字如金的丹师 · MiniMax |
| `san-lang` | 狂刀三浪 | 爱热闹的刀修 · MiniMax |
| `bei-he` | 北河散人 | 稳重调解者 · Agnes |
| `bai-qianbei` | 白前辈 | 神秘寡言 · Agnes |
| `ling-die` | 灵蝶尊者 | 优雅锐利 · MiniMax |

左侧 **ContactList** 点角色 → **私聊**。群气泡 **头像** → **资料卡**。资料页「语音 / 视频」是 **UI 占位**（toast「暂未开放」），**没有音视频信令**。

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  Electron 壳  （唯一入口 start-electron.bat，两阶段）     │
│  阶段 1：lifecycle → :8000 FastAPI + :3000 Next.js       │
│  阶段 2：electron . --no-spawn  （只开窗）               │
│  关窗 → 杀后端 + 前端（没有 stop.bat）                   │
└────────────────────────────┬─────────────────────────────┘
                             │  WS /ws/{session_id}
                             ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI  ·  stream_group_chat / stream_dm_chat          │
│  BehaviorEvent → 评估（六角色一批）                      │
│                → BehaviorEngine.decide()  0..2           │
│                → 串行生成 + WebSocket 推流               │
└────────────────────────────┬─────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   Letta（可选           分角色 LLM         SQLite
   长期 NPC 记忆）       MiniMax / Agnes    记忆 + 决策
                         / OpenAI / …      （只追加）
```

`backend/app/graph.py` 里的 LangGraph Supervisor / 6–8 轮循环是 **兼容遗留**。线上群聊路径是事件驱动的 `stream_group_chat`。

主动发言由进程级 **`BehaviorCoordinator` 单例**（`get_behavior_coordinator()`）负责。`GC_LOOPS_ENABLED` 为 true（默认）时，旧的随机 `XiuzhenCronService` **休眠**，六个 `NpcLoop` **不是** 线上策略。空闲间隔 20–55 秒；每角色每日预算 `GC_DAILY_BUDGET`（默认 60）。

## 混合行为引擎

```text
semantic = 0.24·relevance + 0.20·social_obligation
         + 0.14·relationship_motivation + 0.14·continuity
         + 0.10·persona_impulse + 0.18·novelty_potential

final = clamp(semantic + 确定性加减分)
```

当前默认（可用环境变量调，**不是** 旧 PRD 里的 0.60 / 0.12）：

| 旋钮 | 默认 | 作用 |
| --- | --- | --- |
| `BEHAVIOR_ASSESS_MODE` | `heuristic` | 规则提特征。`@` **永不** 走 LLM 评估。`llm` 恢复六角色特征调用。 |
| `BEHAVIOR_RESPONSE_THRESHOLD` | `0.40` | 低于此值：合格但不入选。 |
| `BEHAVIOR_SECOND_MAX_GAP` | `0.28` | 第二人须分差够近、`novelty_potential ≥ 1`、且 `contribution_key` 不同。 |
| `BEHAVIOR_IDLE_MIN/MAX_SEC` | `20` / `55` | 协调器空闲刺激。 |
| `BEHAVIOR_COOLDOWN_SEC` | `25` | 普通主动冷却（`@` 可覆盖）。 |
| 硬门 | 静音 / 睡眠 / 忙碌 / 已处理 / 日预算 | 压过 `@`。冷却不压过 `@`。 |

`proposed_action=react` 只记意图，**不会** 升级成完整发言（尚无轻量 reaction 协议）。

**可回放审计：**

- `GET /api/behavior/decisions/{event_id}`
- `GET /api/behavior/decisions?session_id=…`
- `POST /api/behavior/decisions/{event_id}/replay` — 只重跑 `decide()`

注入非用户事件：`POST /api/cron/trigger`，body 为 `{"service":"behavior","behavior_event_type":"idle_tick","text":"…"}`。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 桌面 | Electron 34 · `desktop-electron/main.cjs` · lifecycle 之后 `--no-spawn` |
| 界面 | Next.js 15.1.3 · React 19 · Tailwind 3.4 · 深墨金主题 |
| 接口 | Python 3.11+ · FastAPI 0.115+ · Uvicorn · WebSockets |
| Agent | LangChain / LangGraph（生成 + 未上线的旧图）· 可选 Letta |
| 存储 | SQLite + SQLModel — `agent_memory`、`behavior_decisions`（MVP，无生产迁移） |
| LLM | MiniMax / Agnes（分角色）· OpenAI / DeepSeek / Anthropic / Ollama · `USE_MOCK_LLM` |

上下文：最近 20 条完整保留，更早的可摘要（MiniMax）。生成：墙钟 90 秒、正文硬顶 600 字。

## 运行

**真实入口——只有这一条：**

```bat
start-electron.bat
```

可选：`start-electron.bat debug`（可见宿主 + `desktop-electron/launch.log`）· `start-electron.bat rebuild`（先重建前端再启动）。

两阶段启动（`scripts/start-electron.ps1` + `scripts/groupchat-lifecycle.ps1`）：

1. 启动或复用 FastAPI `:8000` 与 Next `:3000`（清孤儿端口；lifecycle 写入 `frontend/public/runtime-config.js`）。
2. 用 `--no-spawn` 打开 Electron。关窗执行 lifecycle **stop**，释放两个端口。

不要把单独的 `uvicorn` / `next dev` / Tauri `desktop-launcher` 当成产品入口。

密钥放在 **已被 gitignore** 的 `.env`（仓库根或 `backend/.env`）。管理后台 ⚙（`POST /api/admin/config`）会把供应商和 key 写进该文件——**禁止提交**。没有 key 则走 mock。

| 变量 | 含义 |
| --- | --- |
| `USE_MOCK_LLM` | 强制 mock，压过 Letta 与真实供应商。 |
| `USE_LETTA` | 默认 true。mock 仍优先。Letta 挂了回退分角色供应商。 |
| `GC_LOOPS_ENABLED` | 默认 true → 启 `BehaviorCoordinator`。`false` → 旧 cron。 |
| `MINIMAX_API_KEY` / `AGNES_API_KEY` / … | 各供应商密钥。 |

## 测试

```powershell
cd backend
uv run ruff check app tests
uv run pytest tests/test_behavior_engine.py tests/test_behavior_coordinator.py tests/test_behavior_audit.py tests/test_group_behavior_integration.py -q

cd ..\frontend
npx tsc --noEmit
```

上述文件覆盖：自然沉默、最多两人、无 LLM 的 `@` 下限、静音压过 `@`、不同 `contribution_key`、冷却 / 预算、三跳停止、只追加日志、确定性回放、协调器单例（一次 idle → 一批评估 → ≤1 人）、重复 `event_id`。

24 小时 soak runner 在 `backend/tests/soak_mvp_candidate.py`。**它不是已通过的产品门。** 真人场景验收（`docs/product/05_MVP_SCENARIO_ACCEPTANCE.md`）同样 **未完成**。

## 诚实的功能开关

| 表面 | 线上默认 | 说明 |
| --- | --- | --- |
| 壁纸 | 静态 CSS `.chat-wallpaper`（深墨金） | `frontend/public/backgrounds/chat-ink-xianxia.png` 在磁盘上，**代码未引用**。 |
| 动态舞台 / 天气 | **关，且未挂载** | 模块在 `frontend/lib/world` 与 `frontend/components/world`。开关：`NEXT_PUBLIC_WORLD_STAGE=1`、`?worldStage=1`、`localStorage xz-world-stage`。`ChatRoom` / `layout` / `page` **没有** import `AppAtmosphere` / `WorldStage` / 测试轮盘。Admin ⚙ **没有**「动态舞台」开关。雨雪不是产品默认。 |
| 语音 / 视频 | 占位按钮 | 只有 toast。无 WebRTC / 信令。 |
| LangGraph 循环 | 未上线 | `stream_group_chat` 走引擎。 |
| `NpcLoop` × 6 | 仅兼容 | 不得重新成为默认主动路径。 |
| Postgres | 未接入 | 只有 SQLite。 |
| 多真人群聊 | 范围外 | 一人 + 六 NPC。 |

## 状态

阶段：**Stage10-World-Stage**，分支 `main`。工程候选：事件驱动评分、可沉默、`@`/DM 兜底、幂等审计回放、单一协调器。

**不宣称已完成：** 24 小时 soak、真人全量验收、离线安装包、真实音视频、生产库迁移。

产品真源：[`docs/product/04_MVP_CANDIDATE_PRD.md`](docs/product/04_MVP_CANDIDATE_PRD.md) · 证据：[`06_MVP_COMPLETION_AUDIT.md`](docs/product/06_MVP_COMPLETION_AUDIT.md)。PRD 里的阈值可能落后于上表环境默认值——**以代码为准**。

## 文档

- [`AGENTS.md`](AGENTS.md) — 当前工作索引
- [`docs/README.md`](docs/README.md) — 文档地图
- [`docs/decisions/0007-npc-self-driven.md`](docs/decisions/0007-npc-self-driven.md) — 主动发言 ADR
- [`docs/screenshots/`](docs/screenshots/) — 视觉证据
