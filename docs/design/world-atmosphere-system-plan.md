# 世界氛围系统详细计划：舞台 × 时间 × 天气 × 程序化云

| 字段 | 值 |
|------|-----|
| **Document** | `docs/design/world-atmosphere-system-plan.md` |
| **日期** | 2026-07-30 |
| **状态** | **Ready for HITL review** — 默认壁纸已还原；氛围系统 flag 默认关；A1–A3+Admin 可测 |
| **基线** | `main`：PR1 固定 host、CSS `WorldStage` 骨架、`jiu-zhou-pavilion/plate.png`、Electron 墨金壳 |
| **关联** | `world-stage-atmosphere.md`（Rev 2.2 结构/flag）、`world-stage-execution-plan.md`（HITL/subagent） |

---

## 0. Owner 决策冻结（2026-07-30）

| # | 议题 | 冻结结论 |
|---|------|----------|
| D1 | **默认天气气质** | **`clear` 为主**，偶发 `mist`（仙气点缀，非常年浓雾） |
| D2 | **时间权威（P1）** | **本地真时钟** `Date` → `timeOfDay`；虚时/后端覆盖留给 P2/P3 |
| D3 | **程序云实现** | **WebGL 全屏 fragment shader**（FBM + domain warp；curl 在 P2）优先于 Pixi / 云贴图 |
| D4 | **鼠标拨雾** | **P1 不做**；**P2 默认开、弱力**（不拦截气泡点击） |
| D5 | **雨/雪** | **P2 末或 P3 门控**；P1 仅 `clear` / `mist` |
| D6 | **默认开 flag** | 仍受 K15 约束：真人验收前 `NEXT_PUBLIC_WORLD_STAGE` 默认关；开发用 `?worldStage=1` |
| D7 | **Plate** | 沿用现有 `public/world/scenes/jiu-zhou-pavilion/plate.png`；若 H-CloudFeel 色冲突再 HITL 换图 |

> 相对 Rev 2.2 的演进：**K2「CSS 多层首发 / Pixi 门控」** → 主路径升级为 **plate（L1）+ WebGL 程序大气（L2）**；CSS 多层保留为 **WebGL 失败 / reduced-motion 回退**，Pixi **非必须**。

---

## 1. 一句话目标

在**不抢聊天气泡**的前提下，把消息区背后做成可持续演化的**修真群世界舞台**：

**固定构图 plate + 程序化雾/云（非云贴图）+ 时段色温状态机 + 晴/雾天气参数 + P2 弱鼠标搅雾**。

---

## 2. 产品定位与硬原则

### 2.1 定位

| 是 | 否 |
|----|-----|
| 对话式游戏的**世界舞台** | IM 壁纸店 |
| 背景**呼吸、偶发事件、可轻交互** | 循环 GIF / 整屏视频贴图云 |
| 中间空、远景有内容 | 近景碎细节抢字 |
| 晴为主、雾偶来 | 常年浓雾糊屏 |

### 2.2 硬原则（验收红线）

1. **结构**：舞台固定，消息滚动（K1/K12 已落地，不可回退）。
2. **可读**：气泡区对比度优先；云/雾密度有上限 + 中心衰减 + L5 遮罩。
3. **不规律**：禁止单一 `sin(t)` / 固定 interval 刷云；用噪声演化 + **泊松事件**。
4. **非贴图云**：云/雾主体由 **噪声场** 生成；plate 只锚定「这是哪片山水」。
5. **可交互（P2）**：弱力拨雾，**绝不拦截**气泡/输入点击。
6. **可关**：`prefers-reduced-motion`、flag、Admin → 静帧 plate。
7. **性能**：Electron 弱机氛围层 20–30 FPS 可接受；后台 tab 降频/暂停。

### 2.3 视觉语言（与 UI 同族）

| Token | 用途 |
|-------|------|
| `#1F1F1F` / `#1A1814` | 深墨底、夜幕 |
| `#C7A969` / `#8E7847` | 暗金高光（极少面积） |
| `#5C7367` | 远山青灰 |
| `#E8E1D4` | 雾中暖边 |

噪声只输出 **密度 0–1**，再经 **时段 LUT** 染成墨金，避免灰写实风景。

---

## 3. 三系统如何咬合（背景 × 时间 × 天气）

```text
                    ┌─────────────────┐
                    │  local clock    │
                    │  (P1 authority) │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  TimeOfDay +    │
                    │  timeBlend      │──► grade LUT（色温/曝光/金边）
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────────┐
        │ Weather  │  │ Cloud    │  │ Plate grade  │
        │ clear/   │─►│ density  │  │ (CSS filter  │
        │ mist…    │  │ events   │  │  or shader)  │
        └──────────┘  └────┬─────┘  └──────────────┘
                           ▼
                    ┌──────────────┐
                    │ WebGL L2     │
                    │ FBM+warp     │
                    │ (+ curl P2)  │
                    └──────┬───────┘
                           ▼
                    L5 readability mask → 气泡可读
```

**合成公式（概念）：**

```ts
visual = grade(timeOfDay, timeBlend)
       × weatherMods(weather, weatherBlend)
       × intensity(group|dm)
       × readabilityCap(uv)

// weatherMods 默认（D1: clear 为主）
// clear: cloudDensity *= 0.55, contrast *= 1.0, gold *= 1.0
// mist:  cloudDensity *= 1.35, contrast *= 0.85, gold *= 0.9
```

**禁止**：为 4 时段 × 4 天气做 16 张整图组合。  
**采用**：一张 plate + 参数表驱动 shader / CSS grade。

---

## 4. 分层架构（最终形态）

```text
message-stage-host (relative, flex-1, min-h-0, overflow hidden)
├── WorldAtmosphere (absolute inset-0, z-0)
│   ├── L0  Sky/Grade      时段色幕 + 暗角（shader uniform 或 CSS）
│   ├── L1  Plate          深墨金远景 plate（cover, horizon ~40%）
│   ├── L2  CloudField     ★ WebGL FBM/Warp（P2 + Curl）
│   ├── L3  MistBreath     可并入 L2 的 density bias
│   ├── L4  WeatherFX      雨/雪（门控，非 P1）
│   └── L5  ReadabilityMask 固定可读遮罩（始终）
└── main messages (overflow-y auto, bg transparent, z-10, pointer-events auto)
```

| 层 | 内容 | 动/静 | 贴图？ | 阶段 |
|----|------|-------|--------|------|
| L0 | 昼夜色温、曝光 | 慢变 | 否 | P1 |
| L1 | 远山/亭台构图 | 静 | **1 张 plate** | 已有 |
| L2 | 云/体积雾 | **程序化主动态** | **否** | P1 基础 / P2 curl |
| L3 | 雾浓度 bias | 慢 | 否 | 并入 L2 |
| L4 | 雨雪 | 事件性 | 否 | 门控 |
| L5 | 遮罩 | 静 | 否 | 已有 CSS |

**结论：** 「云」= L2 算法场，不是 `cloud.png`。Plate 只负责场景身份。

---

## 5. 时间系统（Time of Day）详细设计

### 5.1 状态类型

```ts
type TimeOfDay = "dawn" | "day" | "dusk" | "night";
```

| 时段 | 本地时（默认） | 视觉目标 |
|------|----------------|----------|
| **dawn** | 05:00–07:30 | 冷暖过渡，雾略重，金边弱 |
| **day** | 07:30–17:00 | 对比略高，雾薄（配合 clear 主气质） |
| **dusk** | 17:00–19:30 | 暗金侧光感（LUT），饱和仍低 |
| **night** | 19:30–05:00 | 整体压暗，金屑极少，云对比更低 |

边界可用配置表覆盖，避免硬编码散落。

### 5.2 权威来源

| 阶段 | 来源 | 说明 |
|------|------|------|
| **P1（本计划必做）** | `Date` 本地时钟 | 易验收；与「打开客户端像真实一天」一致 |
| **P2（可选）** | 群内虚时 `groupEpoch` + 加速倍率 | 更游戏；需 Admin 或 debug 控件 |
| **P3** | 后端/剧情 `forceTimeOfDay` | 本阶段不做强绑定 |

### 5.3 插值（禁止硬切）

- 相邻时段混合窗口：**120–180s**（默认 150s）。
- 使用 smoothstep 或线性插值得到 `timeBlend ∈ [0,1]`。
- Shader 暴露：
  - `uTimeOfDay`（可编码为 0–3 float 或 4 维 one-hot 混合权重）
  - `uGradeA` / `uGradeB` 两套 RGB 乘子 + 混合系数

### 5.4 时段 → 视觉参数表（P1 默认）

| 参数 | dawn | day | dusk | night |
|------|------|-----|------|-------|
| `exposure` | 0.92 | 1.0 | 0.88 | 0.72 |
| `warmth`（金边） | 0.35 | 0.25 | 0.55 | 0.12 |
| `cloudContrast` | 0.9 | 1.0 | 0.95 | 0.7 |
| `mistBias`（叠加天气前） | +0.08 | 0 | +0.05 | +0.12 |
| plate `brightness` | 0.95 | 1.0 | 0.9 | 0.75 |
| plate `saturate` | 0.9 | 1.0 | 0.95 | 0.8 |

实现可用 CSS `filter` 调 plate，或全部进 fragment 的 color grade pass。

### 5.5 时钟 tick 策略

- **不**每帧读 `Date` 做状态机切换；每 **1s** 或 **5s** 更新 `timeOfDay` / `timeBlend` 即可。
- 渲染循环只读 store 快照 + `uTime`（performance.now）做噪声动画。

---

## 6. 天气系统（Weather）详细设计

### 6.1 状态类型

```ts
type Weather = "clear" | "mist" | "rain" | "snow";
```

| 天气 | L2 密度 | L4 | 产品频率（D1） |
|------|---------|-----|----------------|
| **clear** | 低 | 无 | **默认主状态** |
| **mist** | 高、软 | 无 | **偶发**（仙气点缀） |
| rain | 中 | 雨丝 | P2 末门控，低频 |
| snow | 中 | 雪 | 更低频或节日 |

**雷电：本阶段不做。**

### 6.2 P1 驱动策略（clear 为主 + 偶发 mist）

```text
每 N 分钟（默认 8–12 min，可 jitter）做一次 weatherRoll：

  baseP(mist) =
    night  → 0.28
    dawn   → 0.22
    dusk   → 0.18
    day    → 0.10

  若当前已是 mist：
    最短持续时间 8–15 min 内禁止切回 clear（防闪烁）
    之后 p(return clear) ≈ 0.65–0.8

  否则：
    roll < baseP(mist) → mist
    else stay/enter clear
```

- **禁止** `setInterval` 固定整点切天气。
- 会话 seed 可微扰 `baseP`，避免所有用户同步起雾（可选）。

### 6.3 天气 → 参数表

```ts
const WEATHER_MODS: Record<Weather, Partial<VisualMods>> = {
  clear: { cloudDensity: 0.55, contrast: 1.0, goldTint: 1.0, alphaCap: 0.35 },
  mist:  { cloudDensity: 1.35, contrast: 0.85, goldTint: 0.9,  alphaCap: 0.48 },
  rain:  { cloudDensity: 1.1,  contrast: 0.9,  goldTint: 0.7,  alphaCap: 0.42 },
  snow:  { cloudDensity: 1.0,  contrast: 0.95, goldTint: 0.85, alphaCap: 0.4  },
};
```

天气切换时 `weatherBlend` 过渡 **20–40s**，与时段 150s 独立。

### 6.4 与时间的组合规则

1. 先算 `timeGrade`，再乘 `weatherMods`，再乘 `intensity`。
2. `mistBias(time)` 与 `weather=mist` **可叠加但有 cap**（例如最终 density ≤ 1.6 × base 前归一化）。
3. night + mist：允许更暗、更糊，但 **中心可读 cap 仍生效**。

### 6.5 P2+ 扩展（不阻塞 P1）

| 项 | 说明 |
|----|------|
| 稀有 rain | 每日最多 0–1 次，或 debug 强制；最短 5 min |
| snow | 节日 flag 或手动 |
| 后端覆盖 | `AtmosphereOverride` 消息类型 — 另开阶段 |

---

## 7. 程序化云层（核心）

### 7.1 算法栈（按阶段）

| 模块 | 算法 | 阶段 |
|------|------|------|
| 形状 | Simplex/OpenSimplex + **FBM** 4 octaves（可降 3） | **P1** |
| 有机感 | **Domain Warping** 1–2 次 | **P1** |
| 运动 | UV 慢漂 + 时间域 FBM 演化 | **P1** |
| 速度场 | **Curl noise → advection** | **P2** |
| 团块 | 可选 Worley × FBM | P2 调参 |
| 出现节奏 | **泊松** density boost events | **P1** |
| 交互 | 鼠标局部力/涡旋叠加速度场 | **P2 默认开弱** |

### 7.2 泊松云事件模型

```ts
type CloudEvent = {
  id: string;
  birth: number;       // ms
  lifetime: number;    // 8_000–40_000 均匀或三角分布
  center: [number, number]; // UV 0–1，偏好中上/两侧，避开死中心
  radius: number;      // 0.12–0.35
  peakDensity: number; // 0.3–0.7
  seed: number;
};
```

| 规则 | 值 |
|------|-----|
| 平均间隔 | 指数分布，均值 **60s**（可配 45–90） |
| 同时存活 | **≤ 2** |
| 峰值包络 | smoothstep 淡入 15% + 平台 + 淡出 25% |
| 禁止 | `setInterval(30_000)` 固定刷云 |

`clear` 下事件 `peakDensity` 再 ×0.7；`mist` 下 ×1.15，且间隔均值可缩短到 ~45s。

### 7.3 着色（墨金）

```text
density = shaped_FBM(uv, t, warp, events) ∈ [0,1]
density *= weatherCloudDensity * timeCloudContrast
density *= readabilityFalloff(uv)   // 中心低、上下/边缘略高
color   = mix(inkBlack, darkGold, density * goldTint(time))
alpha   = density * globalIntensity * weatherAlphaCap
```

`readabilityFalloff` 建议：以屏幕中心椭圆，中心权重 0.55–0.7，边缘 1.0。

### 7.4 鼠标交互（P2，D4 冻结）

| 规则 | 说明 |
|------|------|
| 默认 | **开**，弱力 hover 搅动局部雾 |
| 点击穿透 | 氛围层 **`pointer-events: none`**；鼠标坐标由 **消息层上层透明追踪** 或 window `pointermove` 读取（不抢 hit-test） |
| 推荐实现 | `window`/`stage-host` 上监听 `pointermove`，写入 `mouseUv` store；canvas 永不接收 click |
| 半径 | ~0.10 UV（约 10% 屏宽） |
| 强度 | 弱：速度扰动衰减快，松手 **0.6–1.0s** 回场 |
| reduced-motion | 关闭鼠标扰动与 curl 动画 |

### 7.5 降级矩阵

| 条件 | 行为 |
|------|------|
| `prefers-reduced-motion` | 冻一帧噪声 或 仅 plate + L0 grade + L5 |
| WebGL 初始化失败 | `WorldStageCss` 路径（现有） |
| 持续 FPS < 20（约 3s） | 分辨率 0.5→0.35，octaves 4→3，停事件云 |
| flag off | K12：固定单层生产壁纸/plate，无 shader |
| 文档不可见 / 后台 | `requestAnimationFrame` 暂停或 5–10 FPS |

---

## 8. 状态机与前端数据流

### 8.1 状态形状

```ts
type AtmosphereState = {
  timeOfDay: TimeOfDay;
  timeBlend: number;       // 与下一档 0..1
  weather: Weather;
  weatherBlend: number;    // 切换中 0..1
  intensity: number;       // 群 0.55 / DM 0.4
  interactive: boolean;    // P2：默认 true（弱拨雾）
  seed: number;            // 会话级
  mouseUv: [number, number] | null;
  reducedMotion: boolean;
  engine: "webgl" | "css" | "static";
};

// 每帧 / 每 tick 派生
uniforms = deriveShaderUniforms(state, performance.now())
```

### 8.2 写者与读者

| 模块 | 职责 |
|------|------|
| `atmosphereStore` | 时钟 tick、weatherRoll、mouse、seed；可 zustand / 轻 context + rAF 外订阅 |
| `AtmosphereProvider` | 挂在 ChatRoom/DM stage host 内或 app 级；订阅 visibility / reduced-motion |
| `WorldAtmosphere` | 创建 WebGL canvas、编译 shader、rAF 绘制 |
| `WorldStage` | 门面：`engine=auto` → 试 WebGL → 失败 CSS |
| `poissonCloudEvents` | 纯函数/类：根据 now + weather 维护 events[] |

**P1–P2 不做**：聊天 mood 启发式（沉默→加雾）。留给 P3。

### 8.3 Flag 契约（继承 + 扩展）

| 来源 | 行为 |
|------|------|
| `?worldStage=1` | 开多层/程序大气 |
| `?worldStage=0` | 强制关，仅 K12 静帧 |
| `localStorage xz-world-stage` | Admin「动态舞台」 |
| `NEXT_PUBLIC_WORLD_STAGE===1` | 构建默认开（仅 K15 后） |
| 可选 `?worldEngine=css\|webgl` | 强制引擎（调试） |

优先级仍：**URL > localStorage > env**。

---

## 9. 技术栈与文件落点

### 9.1 技术选择（冻结）

| 组件 | 技术 | 理由 |
|------|------|------|
| 壳 | 现有 `message-stage-host` + Electron | 已落地 |
| L1 | `plate.png` + cover/horizon 40% | 构图锚 |
| L2 | **WebGL2 全屏 triangle + fragment** | 非贴图、可交互、无 Pixi 体积 |
| L0/L5 | shader 内或 CSS 叠层 | L5 可继续用现有 mask class |
| L4 | 同 canvas 第二贡献或轻粒子 | 门控 |
| 状态 | `frontend/lib/world/atmosphereStore.ts` | 无后端依赖 |
| Flag | 扩展 `featureFlags.ts` | 兼容现有 |

**不新增** `pixi.js` 除非未来另开 HITL。Shader 源码可用 TS 字符串内联（免 loader 配置）或 `*.glsl` + raw import（若项目已有）。

### 9.2 目标目录

```text
frontend/
  components/world/
    WorldStage.tsx                 # 门面：auto → webgl | css
    WorldStageCss.tsx              # 回退（已有，可加 time grade filter）
    WorldAtmosphere.tsx            # 新：WebGL host
    AtmosphereProvider.tsx         # 新：可选，或并入 store hook
    shaders/
      atmosphere.vert.ts           # 全屏三角
      atmosphere.frag.ts           # FBM + warp + grade + events
  lib/world/
    featureFlags.ts                # 已有，扩展 engine 调试
    types.ts                       # + TimeOfDay, Weather, AtmosphereState
    atmosphereStore.ts             # 新
    atmosphereParams.ts            # 新：时段/天气参数表
    poissonCloudEvents.ts          # 新
    deriveUniforms.ts              # 新：state → GPU uniforms
  public/world/scenes/jiu-zhou-pavilion/
    plate.png
    manifest.json                  # 可增加 grade 元数据（可选）
```

### 9.3 与现有代码关系

| 已有 | 动作 |
|------|------|
| `ChatRoom` / `DMWindow` stage host | **保留**；Provider/Atmosphere 挂 absolute 层 |
| `WorldStage` → 仅 Css | 改为 auto 选择 |
| `WorldStageCss` | WebGL 失败 / reduced-motion 回退；P1 可先给 plate 加 CSS grade |
| 不透明气泡 / composer 高度上限 | **保持**（可读与防挤舞台） |
| `intensity` 群 0.55 / DM 0.4 | **保持** |

---

## 10. 分阶段交付（详细 PR 计划）

### Phase 0 — 基线（已完成 / 残余）

| 项 | 状态 |
|----|------|
| 固定 stage host | ✅ |
| 不透明气泡、输入不挤舞台 | ✅ |
| Electron 大窗 + 墨金标题栏 | ✅ |
| plate 入库 `jiu-zhou-pavilion` | ✅（D7 沿用；H-CloudFeel 后可换） |
| CSS WorldStage + flag | ✅ 骨架 |

---

### Phase 1 — 状态机 + 静默程序雾（无鼠标）

#### PR-A1 · `feat(world): atmosphere state (time + clear/mist)` ✅ 代码已合入工作区

**目标：** 可测的时间/天气状态，先驱动 **plate 色温**（CSS 即可），为 shader 供数。

**允许改：**

- `frontend/lib/world/types.ts`
- `frontend/lib/world/atmosphereStore.ts`（新）
- `frontend/lib/world/atmosphereParams.ts`（新）
- `frontend/components/world/WorldStageCss.tsx`（读 store：filter/opacity）
- `frontend/components/world/WorldStage.tsx`（挂 Provider 若需要）
- 可选极小测：`frontend/lib/world/__tests__/atmosphereParams.test.ts`（纯函数时段边界）

**禁止：** 行为引擎、后端、Pixi、鼠标层。

**实现要点：**

1. `getTimeOfDay(date: Date): { tod, blend }` 纯函数 + 边界单测。
2. `weatherRoll` 可注入 `rng` + `now`，便于单测 clear 主导概率。
3. 1s interval 更新 store；`visibilitychange` 时校准。
4. CSS：`filter: brightness() saturate() hue-rotate?` 按 grade；雾天气略抬 mid 层 opacity（若有）。

**验收：**

- [ ] 改系统时间或 mock clock，UI plate 色温可区分 day/night。
- [ ] 长跑 30min+（可加速 clock）以 clear 为主，mist 偶发且不闪烁。
- [ ] `npx tsc --noEmit` 干净。
- [ ] flag=0 时无额外动效回归。

---

#### PR-A2 · `feat(world): procedural cloud field WebGL (FBM+warp)` ✅ 代码已合入工作区（待 H-CloudFeel）

**目标：** 半分辨率全屏程序雾；泊松事件；完整降级。

**允许改：**

- `frontend/components/world/WorldAtmosphere.tsx`（新）
- `frontend/components/world/shaders/*`（新）
- `frontend/lib/world/poissonCloudEvents.ts`（新）
- `frontend/lib/world/deriveUniforms.ts`（新）
- `WorldStage.tsx` 接入 auto 引擎
- `featureFlags.ts` 可选 `worldEngine` 调试

**依赖：** A1 merge（或同分支串行但接口先冻结）。

**实现要点：**

1. WebGL2 context；失败 → Css。
2. 内部 FBO 或直接以 `drawingBuffer = cssSize * 0.5~0.75` 绘制再 CSS scale。
3. Fragment：hash/simplex + FBM 4 + domain warp 1–2；`uTime` 慢演化。
4. 最多 2 个 cloudEvent 以 uniform 数组上传（固定槽位，空槽 radius=0）。
5. `readabilityFalloff` + `alphaCap`；中心不糊字。
6. reduced-motion：不跑 rAF 动画，或 blit 静态一帧。
7. `document.hidden` 停 rAF。

**验收（H-CloudFeel）：**

- [ ] 3–5 分钟观看：**无**明显循环节拍、无贴图平铺感。
- [ ] 气泡与空状态文案仍清晰（对比不劣于开之前）。
- [ ] WebGL 禁用/失败时自动 CSS，无白屏。
- [ ] Electron 空闲 CPU/GPU 可接受（主观 + 可选 perf 记录）。
- [ ] `tsc` 干净。

---

### Phase 2 — Curl + 弱鼠标 +（可选）雨雪门控

#### PR-A3 · `feat(world): curl advection + weak mouse stir (default on)`

**目标：** 云运动更有机；弱拨雾默认开且不挡点击。

**实现要点：**

1. Curl noise 速度场扰动 UV 或密度平流近似。
2. `pointermove` → `mouseUv`（host 或 window）；**canvas `pointer-events: none`**。
3. 扰动强度默认弱；松手 0.6–1s 指数衰减。
4. `interactive` 可被 Admin 关；reduced-motion 强制关。

**验收：**

- [ ] 快速连点气泡/输入无丢点击。
- [ ] 拨雾可见但「轻轻搅」而非清空半屏。
- [ ] 关 interactive 后纯自动演化。

---

#### PR-A4 · `feat(world): rare rain/snow`（**门控**，H-Weather 通过后）

- 参数表 + 极低 roll 或 debug 强制。
- 雨丝：1D 噪声或少量实例；预算严格。
- **默认可仍只 clear/mist**，rain/snow 仅 flag 或季节。

---

### Phase 3 — 产品化

| PR | 内容 | 门控 |
|----|------|------|
| **PR-A5** | Admin「动态舞台」+ 可选「拨雾」开关；acceptance 文档补截图 | — |
| **PR-A6** | 考虑 default-on | **K15**：acceptance 文件 + 真人试玩记录 |

### 明确不做（本计划范围外）

- 剧情 scene 切换 / 多场景地图  
- 雷电、Wallpaper Engine、客户端实时 AI 出图  
- 改 BehaviorEngine / Coordinator  
- 循环 WebM 背景  
- 强制引入 Pixi  

---

## 11. 参数预算（实施默认值）

| 参数 | 默认 | 说明 |
|------|------|------|
| Shader 分辨率缩放 | **0.5–0.75** | 再 CSS 放大 |
| FBM octaves | **4**（可降 3） | |
| 目标氛围 FPS | **24–30** | 后台 0 或 5–10 |
| 事件云同时数 | **≤2** | |
| 事件均值间隔 | **60s**（clear）/ **45s**（mist） | 指数分布 |
| clear 密度乘子 | **0.55** | D1 |
| mist 密度乘子 | **1.35** | |
| mist 最短持续 | **10 min** | 防闪 |
| weatherRoll 周期 | **~10 min** + jitter | |
| 时段混合窗 | **150s** | |
| 天气混合窗 | **30s** | |
| 鼠标半径 | **0.10 UV** | P2 |
| 鼠标力 | **弱** | P2 默认开 |
| 群 intensity | **0.55** | |
| DM intensity | **0.40** | |
| 中心 density 衰减 | **有** | 保气泡 |

---

## 12. 风险与缓解

| 风险 | 级 | 缓解 |
|------|----|------|
| 噪声抢字 | 高 | 密度曲线、中心衰减、L5、不透明气泡、alphaCap |
| 规律循环感 | 中 | 泊松 + 多 seed + P2 curl |
| 性能（Electron） | 中 | 半分辨率、减 octave、自动降级、后台停 |
| 鼠标挡点击 | 中 | canvas 永不 hit；只读 pointermove |
| plate 与 shader 色冲突 | 中 | 统一 LUT；plate 只 L1 |
| mist 过频违背 D1 | 中 | baseP(day)=0.10 + 最短持续 + 单测分布 |
| 范围膨胀 | 中 | 严格 Phase；雨雪门控 |
| Rev 2.2 文档漂移 | 低 | 本文件为大气主路径权威；atmosphere.md 注「L2 演进见本计划」 |

---

## 13. HITL 节点

| ID | 时机 | 人决策 | 状态 |
|----|------|--------|------|
| H-Decisions | 计划前 | 天气/时钟/WebGL/鼠标 | **已冻结 → §0** |
| H-Plate | 若色冲突 | 换 plate 或重出图 | 按需 |
| **H-CloudFeel** | PR-A2 后 | 雾是否自然偶尔、是否抢字 | **待** |
| H-Mouse | PR-A3 后抽检 | 弱拨雾是否合适（默认已定开） | 调参即可 |
| H-Weather | PR-A4 前 | 是否上雨雪 | 门控 |
| H-DefaultOn | PR-A6 | 真人验收后才默认开 | K15 |

---

## 14. 执行编排（Worktree / Subagent）

| 任务 | 执行者 | isolation | 依赖 |
|------|--------|-----------|------|
| PR-A1 状态机 + plate grade | impl subagent | worktree 建议 | Phase 0 |
| PR-A2 WebGL 云 | impl subagent（图形向） | **worktree** | A1 接口 |
| PR-A3 curl + mouse | 同 A2 后续 | worktree | A2 + H-CloudFeel |
| PR-A4 雨雪 | 仅 H-Weather 后 | worktree | A3 |
| 验收截图 / tsc | qa 或主会话 | 只读 | 各 PR 后 |
| 合并 main | 编排 | — | 评审通过 |

**并行：** A1 与「plate 审美微调」可并行；**不可**两 agent 同时改 `WorldStage.tsx` 无协调。

**建议 commit 信息：**

- `feat(world): atmosphere time/weather store and plate grade`
- `feat(world): webgl FBM cloud field with poisson events`
- `feat(world): curl advection and weak mouse fog stir`

---

## 15. 成功标准（整体 DoD）

1. 背景是**固定舞台**，消息滚动不影响氛围层。  
2. 云/雾 **程序化**，无明显贴图平铺与固定节拍。  
3. **时段**可感知（至少 day vs night 色温差）。  
4. **天气** clear 为主、mist 偶发；切换柔和。  
5. P2 弱拨雾默认开时 **不破坏** 点气泡/输入。  
6. 弱机可降级；reduced-motion 可静帧。  
7. 色调与深墨金 UI **同族**。  
8. flag 可完全关掉程序层（K12 静帧仍在）。  
9. 无后端强依赖；行为引擎零改动。  

---

## 16. 最小竖切（确认后立刻开工的顺序）

在不推翻 PR1 / 现有 plate 的前提下：

1. **A1**：`atmosphereStore` + 时段纯函数 + clear/mist roll + plate CSS grade。  
2. **A2**：半分辨率 WebGL FBM+warp 叠 plate；泊松 0–2 团；降级矩阵。  
3. 截图 / Electron 实机 → **H-CloudFeel**。  
4. 通过后 **A3**：curl + window pointermove 弱拨雾。  
5. Admin / acceptance / 默认开按 Phase 3 与 K15。  

---

## 17. 与旧文档关系

| 文档 | 关系 |
|------|------|
| `world-stage-atmosphere.md` Rev 2.2 | **结构、flag、K12/K15、资产管线** 仍有效；L2 从「CSS 多层 / Pixi」**演进**为本计划 WebGL 程序大气 |
| `world-stage-execution-plan.md` | HITL/subagent 方法论复用；PR 编号以本文件 **PR-A\*** 为准（大气轨道） |
| 本文件 | **时间 × 天气 × 程序云** 的实施权威 |

---

## 18. 背景美术何时做？（产品答）

| 阶段 | 做什么 | 何时 |
|------|--------|------|
| **现在（默认路径）** | 生产壁纸 **还原** 为 `chat-ink-xianxia.png`；氛围 **默认关** | ✅ 已做 |
| **系统审核（本轮）** | 时段/天气/WebGL 雾/调试轮盘/Admin 开关 — 功能与性能 | 你现在审核 |
| **背景设计 HITL** | 从 `docs/screenshots/world-stage-candidates/` 选 plate，或重出图；替换 `public/world/.../plate.png` | **系统通过后再做**，不挡功能合入 |
| **默认开 flag** | K15：真人验收 + acceptance 文档 | 美术定稿后 |

原则：**代码与开关可先合；好看的底板是单独审美决策**，不要用实验 plate 污染默认聊天观感。

---

## 19. 实现完成度（审核清单）

| 项 | 状态 |
|----|------|
| 默认壁纸还原 ink-xianxia | ✅ |
| 历史消息头像可进资料 | ✅ |
| A1 时段 + clear/mist | ✅ |
| A2 WebGL FBM+warp + 泊松 | ✅ |
| A3 curl + 弱拨雾 | ✅ |
| 测试轮盘（右上角） | ✅ |
| Admin「动态舞台」 | ✅ |
| 雨雪粒子 L4 | ⏸ 门控未做（参数表已有） |
| 默认开 flag | ⏸ K15 |
| 新 plate 美术 | ⏸ HITL 另开 |

---

**文档结束。**
