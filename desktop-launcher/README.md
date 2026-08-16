# 九洲一号群 桌面启动器 (Tauri 2)

> 九洲一号群聊天群专用桌面启动器 — 把已经能跑的 Next.js 九洲一号群 web 端 **套壳** 为 Tauri 2 桌面应用, 套上 **QQ/微信/Discord 三栏聊天布局**, 加 QQ 风的 **关闭=最小化到托盘 + 全局快捷键 Ctrl+Shift+Q**.

> 本目录 (`desktop-launcher/`) 是 **独立的 Tauri 2 项目**, 与 `frontend/` (Next.js) 和 `backend/` (FastAPI) **完全分离, 零改动**. 九洲一号群本体代码一字未动.

## 技术栈

| 层 | 技术 | 版本 |
| --- | --- | --- |
| 壳 | Tauri | 2.11 (tray-icon feature) |
| 前端 | React + TypeScript + Vite + Tailwind v3 | 18 / 5.4 / 3.4 |
| 全局快捷键 | `tauri-plugin-global-shortcut` | 2.3 |
| 聊天嵌入 | `<iframe>` 指向 `http://localhost:3000` | — |

## 架构图

```
┌─ Tauri 2 窗口 (1100×700, 标题: 九洲一号群) ──────────────────────┐
│  ┌── TopBar 60px ────────────────────────────────────────────┐  │
│  │ 📜 九洲一号群 · 九洲一号群聊天群 · 6 友在线     🟢 Connected    │  │
│  └──┬─────────────┬───────────────────────────────┬──────────┘  │
│     │             │                                │             │
│  ┌──▼──────┐  ┌───▼─────────────┐  ┌──────────────▼────────┐    │
│  │ 左侧栏  │  │ 中栏 (iframe)     │  │ 右栏 — 6 角色成员       │    │
│  │ 280px   │  │ flex-1            │  │ 240px                  │    │
│  │         │  │                   │  │                         │    │
│  │ 📜 九洲一号群 │  │ http://localhost │  │ 🌟 宋书航 灵尊         │    │
│  │ 🗒️ 纪要 │  │      :3000       │  │ 💊 药师 八品药         │    │
│  │ ⚙️ 设置 │  │ (九洲一号群 web)     │  │ 🗡️ 狂刀三浪 六品刀      │    │
│  │         │  │                   │  │ 🌊 北河散人 八品散      │    │
│  │ + 新群  │  │                   │  │ 👻 白前辈 九品上        │    │
│  │ (P2)    │  │                   │  │ 🦋 灵蝶尊者 八品尊       │    │
│  └─────────┘  └───────────────────┘  └─────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                       │                │              │
                       ▼                ▼              ▼
                  frontend/         backend/        (iframe)
                  Next.js 15        FastAPI
                  :3000             :8000 (九洲一号群)
```

## 启动方式

### 前置

九洲一号群 web 端必须先起来:

```bash
# terminal A — 九洲一号群后端
cd C:\Users\yyy\Desktop\简历工作流\项目B_GroupChat\backend
python -m uvicorn app.main:app --reload --port 8000

# terminal B — 九洲一号群前端
cd C:\Users\yyy\Desktop\简历工作流\项目B_GroupChat\frontend
npm run dev   # 跑在 :3000
```

### 启动器 (Tauri 2)

```bash
cd C:\Users\yyy\Desktop\简历工作流\项目B_GroupChat\desktop-launcher
npm install
npm run tauri dev   # 编译 + 弹出 1100x700 桌面窗口
```

第一次 `cargo` 编译约 40s, 之后增量 < 5s.

### 验证清单 (Phase 4 验证)

| # | 检查 | 命令 / 操作 |
| --- | --- | --- |
| 1 | `npm run build` 通过 (frontend) | `npm run build` |
| 2 | `cargo check` 通过 (Rust) | `cd src-tauri && cargo check` |
| 3 | 1100×700 窗口弹出 | `npm run tauri dev` |
| 4 | 三栏布局 | 左 280 / 中 iframe / 右 240 |
| 5 | 6 角色成员显示 | 右栏列出 宋书航/药师/狂刀三浪/北河散人/白前辈/灵蝶尊者 + emoji + 境界 |
| 6 | iframe 九洲一号群能聊天 | 中栏嵌 `http://localhost:3000` |
| 7 | 启动 splash | 冷启动显示 📜 logo + 进度条, ~1.1s 后淡出 |
| 8 | 关闭 = 最小化到托盘 | 点 ✕ → 窗口消失 → 托盘图标保留 |
| 9 | 托盘菜单 | 显示 / 隐藏 / 退出 |
| 10 | Ctrl+Shift+Q 唤起 | 全局快捷键 (任何应用下都生效) |
| 11 | web 端独立可用 | `localhost:3000` 仍可直接访问 |

## 目录结构

```
desktop-launcher/
├── src/                          # React 前端
│   ├── App.tsx                   # 三栏布局 + splash 编排
│   ├── Splash.tsx                # 启动屏 (logo + 进度条)
│   ├── TopBar.tsx                # 60px 顶栏 (含 ConnectionIndicator)
│   ├── GroupSidebar.tsx          # 左栏 280px
│   ├── MemberList.tsx            # 右栏 240px (6 角色)
│   ├── ChatIframe.tsx            # 中栏 iframe + 加载兜底
│   ├── Avatar.tsx                # 头像组件
│   ├── ConnectionIndicator.tsx   # 后端探针 (5s 轮询 /health)
│   ├── roles.ts                  # 九洲一号群 6 角色元数据 (与 frontend/lib/ws.ts 对齐)
│   ├── index.css                 # Tailwind + 自定义滚动条 + splash 动画
│   └── main.tsx                  # React 入口
├── src-tauri/                    # Rust 后端
│   ├── src/
│   │   ├── main.rs               # Tauri 入口 (tray + global-shortcut + close-to-tray)
│   │   └── lib.rs
│   ├── capabilities/
│   │   └── default.json          # 权限白名单
│   ├── icons/                    # 启动器图标 (PIL 生成的 📜 amber gradient)
│   ├── tauri.conf.json           # 1100×700 + identifier com.xiuzhen.launcher
│   ├── Cargo.toml
│   └── build.rs
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json / tsconfig.node.json
├── postcss.config.js
└── index.html
```

## 设计决策

1. **iframe 嵌入而非重写聊天**: 九洲一号群 web 端已经能跑, 套壳成本 < 重写成本. iframe + sandbox 让聊天 100% 复用, 九洲一号群 6 角色 emoji + 聊天气泡 + @mention + 时间分组全保留.
2. **启动 splash**: 前端 React 控制 — `<Splash>` 组件完成后通过 `@tauri-apps/api/window` 的 `getCurrentWindow().show()` 触发 Rust 显示窗口. 这样可以 100% 避免 Tauri 默认白屏闪烁.
3. **关闭 = 最小化 (QQ 风)**: Rust 端 `on_window_event` 拦截 `CloseRequested`, `prevent_close()` + `hide()`. 真正的 exit 只能从托盘菜单的"退出"按钮或 Ctrl+C 中断触发.
4. **全局快捷键**: `tauri-plugin-global-shortcut` 在 setup 阶段注册 `Ctrl+Shift+Q` → 任何应用按下都会唤起主窗口 (Rust `show + unminimize + set_focus`).
5. **装饰标准窗口** (`decorations: true`): spec 推荐 P1 先做标准窗口, 自定义标题栏 P2.
6. **不引入新框架**: 严格按 spec 的 Tauri 2 + React 18 + TS + Tailwind v3.

## 与九洲一号群 web 端的关系

- **0 改动** `frontend/` 和 `backend/`
- `desktop-launcher/src/roles.ts` 是元数据的 1:1 镜像, 仅用于右栏展示, 不参与聊天逻辑
- 聊天 100% 由 iframe 内的九洲一号群处理
- 九洲一号群 6 角色头像 emoji 一字未改: 🌟 💊 🗡️ 🌊 👻 🦋

## 已知限制 / 后续 (P2)

- 左侧 tab "纪要/设置" 是 placeholder, 没有真实功能
- "+ 新建群" 按钮 disabled
- 没打 release exe (`tauri build` 出 `.msi/.exe` 留给 P2)
- 系统托盘只有 show/hide/quit 三项
- 没做最小化动效 (QQ 风渐隐)

## License

Private / 仅本机使用.