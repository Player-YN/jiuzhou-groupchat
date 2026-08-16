import { useState } from "react";
import ConnectionIndicator from "./ConnectionIndicator";

type Tab = "chat" | "notes" | "settings";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "chat", label: "聊天", icon: "📜" },
  { key: "notes", label: "纪要", icon: "🗒️" },
  { key: "settings", label: "设置", icon: "⚙️" },
];

/** GroupSidebar — 启动器左栏（深墨金主题, Stage 8） */
export default function GroupSidebar() {
  const [active, setActive] = useState<Tab>("chat");

  return (
    <nav className="w-[280px] shrink-0 bg-ink-800 border-r border-gold-500/20 flex flex-col">
      <div className="h-12 px-3 flex items-center border-b border-gold-500/15 shrink-0">
        <span className="text-[10px] text-ink-dim uppercase tracking-widest font-xiuzhen-title">
          九洲一号群列表
        </span>
      </div>

      <ul className="flex-1 overflow-y-auto py-2 space-y-0.5">
        {TABS.map((t) => (
          <li key={t.key}>
            <button
              type="button"
              onClick={() => setActive(t.key)}
              className={[
                "w-full px-4 py-2.5 flex items-center gap-3 text-left transition-colors",
                "border-l-2",
                active === t.key
                  ? "bg-ink-700 border-gold-500 text-ink-text shadow-[inset_0_0_0_1px_rgba(199,169,105,0.18)]"
                  : "border-transparent text-ink-muted hover:bg-ink-700/60 hover:text-ink-text",
              ].join(" ")}
            >
              <span className="text-base">{t.icon}</span>
              <span className="text-sm font-medium font-xiuzhen-body">{t.label}</span>
              {active === t.key && (
                <span className="ml-auto h-1.5 w-1.5 rounded-full bg-gold-500 dot-pulse" />
              )}
            </button>
          </li>
        ))}

        <li className="px-4 pt-3 pb-1">
          <div className="text-[10px] uppercase text-ink-dim tracking-widest font-xiuzhen-title">
            当前活跃
          </div>
        </li>
        <li>
          <div className="mx-3 px-3 py-2 rounded-md bg-gradient-to-br from-gold-500/15 to-gold-500/5 border border-gold-500/30 shadow-inner-gold">
            <div className="text-sm font-semibold text-ink-text flex items-center gap-2 font-xiuzhen-body">
              <span>📜</span> 九洲一号群聊天群
            </div>
            <div className="text-[11px] text-ink-muted mt-0.5">
              九洲一号 · 6 友在线
            </div>
          </div>
        </li>
      </ul>

      <footer className="px-3 py-2 border-t border-gold-500/15 shrink-0">
        <button
          type="button"
          disabled
          className="w-full px-3 py-2 rounded-md text-xs font-medium text-ink-dim bg-ink-700/50 cursor-not-allowed ring-1 ring-gold-500/10"
          title="P2 阶段提供"
        >
          + 新建群
        </button>
      </footer>
    </nav>
  );
}

/** TopBar — 启动器顶栏（深墨金主题, Stage 8） */
export function TopBar() {
  return (
    <header className="h-[60px] shrink-0 bg-ink-800 border-b border-gold-500/20 flex items-center px-5 gap-4 shadow-md shadow-black/40">
      {/* 群图标 — 金光渐变 + 仙侠书法 */}
      <div className="h-9 w-9 rounded-md bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-md shadow-gold-500/30 ring-1 ring-gold-400/40">
        <span className="font-xiuzhen-title text-base text-ink-900">九</span>
      </div>

      {/* 群名 + 副标 — 金光流 */}
      <div className="flex flex-col leading-tight min-w-0">
        <span className="text-base font-bold gold-text truncate font-xiuzhen-title">
          九洲一号群
        </span>
        <span className="text-xs text-ink-muted truncate">
          九洲一号群聊天群 · 6 友在线
        </span>
      </div>

      {/* 状态指示 — 中右 */}
      <div className="ml-auto">
        <ConnectionIndicator />
      </div>

      {/* 装饰按钮 — QQ 风格 */}
      <div className="flex items-center gap-1 ml-3 text-ink-muted">
        <button
          type="button"
          className="h-8 w-8 rounded-md hover:bg-ink-700 hover:text-gold-400 flex items-center justify-center transition-colors"
          title="设置 (P2)"
        >
          ⚙️
        </button>
      </div>
    </header>
  );
}
