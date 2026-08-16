# AGENTS.md — 项目 B 工作索引

> 本文件采用**渐进式披露**：默认只读第 1～3 节；只有任务确实涉及相应领域时，再展开第 4 节或历史档案。每次代码、规格或验证状态变化后，更新本文件的“当前状态”和对应的详细文档。

## 1. 当前状态（默认必读）

- 产品：**单个真人用户 + 6 个固定小说角色 AI** 的持续群聊社交游戏；核心价值是可信、持续的群体社交感，不是脑暴或会议纪要。
- 当前阶段：`Stage10-World-Stage`（默认分支 **`main`**）。设计：`docs/design/world-stage-atmosphere.md` + 执行手册 `world-stage-execution-plan.md`。
- **桌面入口（唯一）**：根目录 **`start-electron.bat`**（静默开窗）。可选：`start-electron.bat debug` / `start-electron.bat rebuild`。**关窗自动杀后端/前端进程**（无 stop.bat）。日志：`desktop-electron/launch.log`。
- **背景**：深墨金静底（`.chat-wallpaper`）；**已下线**雨雪/时段氛围与测试轮盘（`docs/design/world-*` 仅作历史设计）。
- **背景默认**：生产壁纸 **还原** `frontend/public/backgrounds/chat-ink-xianxia.png`（flag 关时）；实验 plate/程序雾 **默认关**。
- **World Stage + 氛围**（flag / Admin「动态舞台」/ `?worldStage=1`）：时段+天气+WebGL FBM/curl+弱拨雾；右上角测试轮盘。计划 `docs/design/world-atmosphere-system-plan.md`。**背景美术 HITL 另开**（候选图 `docs/screenshots/world-stage-candidates/`）。历史消息头像可点资料。雨雪粒子 / 默认开 flag 未做。
- **侧栏 UX**：ContactList 点角色 → **私聊**；群聊气泡头像 → **资料主页**。
- **行为调参（更热闹）**：默认 **规则启发式** 提取意图（`BEHAVIOR_ASSESS_MODE=heuristic`，**@ 从不走 LLM 评估**）；`response_threshold` **0.40**；`second_max_gap` **0.28**；idle **20–55s**；仍最多 2 人 / 可沉默。恢复旧路径：`BEHAVIOR_ASSESS_MODE=llm`。
- 已实现（继承 candidate）：事件驱动混合评分、自然沉默、@/DM 确定性兜底、幂等/审计回放、单一 `BehaviorCoordinator`。
- 质量门（Stage10 本批）：behavior 相关 pytest **28/28**；`npx tsc --noEmit` 干净；Electron `main.cjs` syntax OK。全量回归与 24h soak 仍待真人验收。
- 本地端口：固定 `8000` / `3000`；`frontend/public/runtime-config.js` 由 lifecycle 写入。
- 不做：音视频真实信令、Electron 内嵌 Python 安装包、离线 LLM-as-Judge、多真人群聊、生产 DB 迁移。

## 2. 读取路径（按任务展开）

### 产品与验收

1. `docs/product/04_MVP_CANDIDATE_PRD.md` — 范围、评分、DoD、停止扩张边界。
2. `docs/product/05_MVP_SCENARIO_ACCEPTANCE.md` — 34 条真人场景与通过阈值。
3. `docs/product/06_MVP_COMPLETION_AUDIT.md` — 每条 DoD 的权威证据和未完成项。

### 代码与运行时

- 群聊行为：`backend/app/behavior/engine.py`、`backend/app/graph.py`。
- 主动行为：`backend/app/scheduler/behavior_coordinator.py`、`backend/app/scheduler/lifespan.py`。
- WebSocket / DM：`backend/app/routers/ws.py`、`frontend/lib/ChatContext.tsx`、`frontend/lib/useDmSession.ts`。
- 可回放审计：`backend/app/routers/behavior_audit.py`。
- 稳定性 runner：`backend/tests/soak_mvp_candidate.py`。

### 历史与背景（非默认读取）

- 旧研究、架构与阶段计划：`docs/architecture/`。
- 历史审计：`docs/audits/`。
- 设计研究：`docs/research/`；截图证据：`docs/screenshots/` 与 `frontend/docs/`。
- 详细阶段日志与踩坑：`docs/history/AGENTS_HISTORY_2026-07.md`。仅在追溯旧提交、旧决策或迁移兼容时读取。

## 3. 不可推翻的决策

1. LLM 只做批量语义特征提取；确定性规则负责最终评分、仲裁、冷却、预算和上限。
2. 普通群聊事件最多两人响应，也允许零人响应；明确 @ 和 DM 有确定性目标。
3. 行为决策必须持久化、可查询、可纯规则回放；同 event ID 不同输入必须报冲突。
4. 默认只启动一个 `BehaviorCoordinator`；旧 `NpcLoop` 只保留兼容，不可重新成为默认主动路径。
5. SQLite 是 MVP 存储；任何生产迁移另开阶段。

## 4. 操作与验证（按需读取）

### 常用检查

```powershell
cd backend; uv run ruff check app tests; uv run pytest -q
cd frontend; npm run build
```

### 24h soak 修复后的重跑要求

- 使用隔离 SQLite 与 mock LLM，不能污染用户会话数据。
- 每个自然日分别验证主动预算：达到预算后必须静默；跨日可按产品规则恢复额度。
- 持续覆盖：0/2 响应、六角色身份、群聊/DM duplicate、replay、depth-3 停止、协调器存活、legacy cron 休眠。
- 运行产物只放 `.harness/`，完成或失败后将结论写入 `docs/product/06_MVP_COMPLETION_AUDIT.md`，再清理 `.harness/`；不得提交。

### 文件卫生

- `.harness/`、日志、临时 SQLite、浏览器快照和一次性验证报告是过程文件；不提交。
- 可长期复查的 PRD、验收表、审计、ADR、研究和截图放 `docs/` 对应子目录。
- 移动 tracked 文档后必须更新 README、AGENTS 和受影响的相对链接。

## 5. 更新协议

1. 先更新本节的“当前状态”。
2. 再更新 `docs/product/06_MVP_COMPLETION_AUDIT.md` 的证据，不以口头结论替代。
3. 只有实现、测试、文档三者一致时才提交；不要把 `.harness/` 或密钥提交进仓库。
