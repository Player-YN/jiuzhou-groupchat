# desktop-electron — 九洲一号群

Electron 壳默认 **1440×900**（可缩放，min 1280×800），**frameless 墨金标题栏**，直接加载 Next.js 前端。

## 怎么开 / 关

| 操作 | 方式 |
|---|---|
| 启动 | 仓库根目录 **唯一入口** `start-electron.bat`（静默） |
| 停止 | **关闭窗口** → Electron `before-quit` 自动杀后端/前端并释放 8000/3000 |
| 日志 | `desktop-electron/launch.log`、`electron.log` |

**启动流程（两阶段）：**

1. `groupchat-lifecycle.ps1` 起后端 8000 + 前端 3000  
2. 直接运行 `electron.exe . --no-spawn` 打开固定窗（不再只起端口不开窗）

单实例：第二次启动会被拒绝（或聚焦已有窗口）。

## 开发（服务已手动拉起时）

```powershell
# 终端 A
powershell -File scripts/groupchat-lifecycle.ps1 -Action start -Mode electron
# 终端 B
cd desktop-electron
npm run dev   # electron . --no-spawn
```

## 依赖

- 根目录 `scripts/groupchat-lifecycle.ps1`
- `backend/.venv`、前端已 `npm run build`（缺 BUILD_ID 时 lifecycle 会自动 build）
