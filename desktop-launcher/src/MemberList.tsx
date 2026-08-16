import Avatar from "./Avatar";
import { ROLE_LIST, ROLE_META } from "./roles";

/** MemberList — 启动器右栏 6 九洲一号群成员（深墨金主题, Stage 8） */
export default function MemberList() {
  return (
    <aside className="w-[240px] shrink-0 bg-ink-800 border-l border-gold-500/20 flex flex-col">
      <header className="h-12 px-4 flex items-center justify-between border-b border-gold-500/15 shrink-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-ink-text font-xiuzhen-title gold-text">
            群成员
          </span>
          <span className="text-xs text-ink-muted">{ROLE_LIST.length} 友</span>
        </div>
        <button
          type="button"
          className="text-ink-dim hover:text-gold-400 transition-colors"
          title="刷新成员"
          onClick={() => location.reload()}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-3.5 h-3.5"
          >
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
            <path d="M16 16h5v5" />
          </svg>
        </button>
      </header>

      <ul className="flex-1 overflow-y-auto py-2">
        {ROLE_LIST.map((k) => {
          const m = ROLE_META[k];
          return (
            <li
              key={k}
              className="group px-3 py-2 mx-2 rounded-md hover:bg-ink-700 transition-colors flex items-center gap-3 border border-transparent hover:border-gold-500/15"
            >
              <Avatar agentKey={k} size="md" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className={`text-sm font-medium truncate ${m.text}`}>
                    {m.name}
                  </span>
                  <span
                    className="text-[10px] px-1.5 rounded bg-ink-700 text-ink-muted leading-4 ring-1 ring-gold-500/10"
                    title={`Provider: ${m.provider}`}
                  >
                    {m.provider}
                  </span>
                </div>
                <div className="text-[11px] text-ink-dim truncate">
                  {m.realm}
                </div>
              </div>
              <span
                className="h-2 w-2 rounded-full bg-jade shrink-0"
                title="在线"
              />
            </li>
          );
        })}
      </ul>

      <footer className="px-4 py-2 border-t border-gold-500/15 text-[10px] text-ink-dim shrink-0">
        九洲一号群六友 · 同步自后端
      </footer>
    </aside>
  );
}
