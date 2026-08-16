"use client";

/** GroupSidebar — 右侧抽屉，6 角色卡片 (深墨金主题)
 *  - 点击卡片/头像 → onPick 插入 @角色名（不切私聊、默认不弹资料）
 *  - 切私聊请用左侧 ContactList；资料请用群聊消息头像
 *  - 可选 onOpenProfile（父组件默认不传）
 *  - 在线状态：九洲一号群角色常住在线
 */
import { ROLE_META, ROLE_CYCLE, type RoleKey } from "@/lib/ws";
import AgentAvatar from "./AgentAvatar";

type Props = {
  open: boolean;
  onClose: () => void;
  /** 用户点击 @ 提及 → 触发 @ 插入 */
  onPick: (role: RoleKey) => void;
  /** Stage 10: 点击卡片主体 → 打开角色资料 */
  onOpenProfile?: (role: RoleKey) => void;
};

const PRESET_AVATAR_BG: Record<RoleKey, string> = {
  "shu-hang": "from-[#3A2F1A] to-[#2A2620]",
  "yao-shi": "from-[#1F2A26] to-[#2A2620]",
  "san-lang": "from-[#2F1A1A] to-[#2A2620]",
  "bei-he": "from-[#1A2330] to-[#2A2620]",
  "bai-qianbei": "from-[#2A2620] to-[#342F28]",
  "ling-die": "from-[#2A1F2A] to-[#2A2620]",
};

export default function GroupSidebar({ open, onClose, onPick, onOpenProfile }: Props) {
  return (
    <>
      {/* 背景遮罩（仅 sidebar 打开时显示） */}
      <div
        className={[
          "fixed inset-0 z-30 bg-black/60 backdrop-blur-sm transition-opacity duration-200",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        ].join(" ")}
        onClick={onClose}
        aria-hidden
      />

      {/* 抽屉本体 — 深墨金 */}
      <aside
        className={[
          "fixed right-0 top-0 z-40 h-full w-[320px] max-w-[90vw] flex flex-col",
          "bg-gradient-to-b from-xz-bg-2 to-xz-bg",
          "border-l border-xz-border shadow-2xl shadow-black/60",
          "transition-transform duration-300 ease-out",
          open ? "translate-x-0" : "translate-x-full",
        ].join(" ")}
        aria-label="九洲一号群成员"
        aria-hidden={!open}
        data-testid="group-sidebar"
      >
        {/* Header — 金光顶 */}
        <div className="flex items-center justify-between border-b border-xz-border bg-xz-panel/60 px-4 py-3 backdrop-blur">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-[#C7A969] via-[#D4B574] to-[#8E7847] text-sm shadow-inner shadow-[#C7A969]/40 ring-1 ring-[#D4B574]/40">
              <span className="font-xiuzhen-title text-xz-bg">九</span>
            </div>
            <div>
              <div className="font-xiuzhen-title text-sm font-semibold gold-text">九洲一号群</div>
              <div className="text-[10px] text-xz-ink-muted">九洲一号群 6 角色 · 全部在线</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-xz-ink-muted transition hover:bg-xz-panel hover:text-[#D4B574]"
            aria-label="关闭成员列表"
            data-testid="sidebar-close"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </svg>
          </button>
        </div>

        {/* 角色列表 */}
        <div className="flex-1 overflow-y-auto px-3 py-3">
          <div className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-xz-ink-dim">
            members · 群友
          </div>
          <ul className="flex flex-col gap-2">
            {ROLE_CYCLE.map((k) => {
              const meta = ROLE_META[k];
              return (
                <li key={k}>
                  <div
                    data-testid={`sidebar-card-${k}`}
                    data-role={k}
                    className={[
                      "group relative w-full overflow-hidden rounded-xl",
                      "bg-gradient-to-br", PRESET_AVATAR_BG[k],
                      "border border-xz-border px-3 py-2.5 text-left",
                      "shadow-md shadow-black/40",
                      "transition-shadow hover:shadow-lg hover:border-[#C7A969]/40 hover:shadow-[#C7A969]/10",
                    ].join(" ")}
                  >
                    {/* 左侧 4px 角色色条 */}
                    <span
                      className={`absolute left-0 top-0 h-full w-1 bg-gradient-to-b ${meta.accent}`}
                      aria-hidden
                    />

                    <div className="flex items-center gap-3 pl-2">
                      <button
                        type="button"
                        onClick={() => onPick(k)}
                        data-testid={`sidebar-profile-${k}`}
                        className="shrink-0 rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[#C7A969]/50"
                        aria-label={`@${meta.name}`}
                      >
                        <AgentAvatar
                          agentKey={k}
                          size="md"
                          ring
                          showRealmTag
                        />
                      </button>
                      <button
                        type="button"
                        onClick={() => onPick(k)}
                        data-testid={`sidebar-pick-${k}`}
                        className="min-w-0 flex-1 text-left active:scale-[0.99]"
                        aria-label={`在输入框 @${meta.name}`}
                      >
                        <div className="flex items-center gap-1.5">
                          <span className={`text-sm font-semibold ${meta.text} font-xiuzhen-body`}>
                            {meta.name}
                          </span>
                          <span className="h-1.5 w-1.5 rounded-full bg-[#5C7367] ring-2 ring-xz-bg" />
                          <span className="text-[9px] font-medium text-[#7A9387]">在线</span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-xz-ink-muted">
                          <span className="rounded bg-xz-bg/70 px-1.5 py-px font-medium text-xz-ink-muted ring-1 ring-xz-border-soft">
                            {meta.realm}
                          </span>
                          <span className="truncate">{meta.blurb}</span>
                        </div>
                        <div className="mt-1 flex items-center justify-between text-[9px] text-xz-ink-dim">
                          <span className="uppercase tracking-wider">{meta.provider}</span>
                          <span className="opacity-0 transition group-hover:opacity-100 text-[#D4B574]">
                            @ 提及
                          </span>
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onPick(k);
                        }}
                        data-testid={`sidebar-mention-${k}`}
                        title={`@${meta.name}`}
                        aria-label={`在输入框提及 ${meta.name}`}
                        className={[
                          "shrink-0 rounded-lg border border-xz-border bg-xz-bg/50 px-2 py-1.5",
                          "text-[10px] font-semibold text-[#D4B574]",
                          "transition hover:border-[#C7A969]/50 hover:bg-xz-panel",
                          "active:scale-95",
                        ].join(" ")}
                      >
                        @ 提及
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          {/* 底部 footer：使用说明 */}
          <div className="mt-4 rounded-lg border border-xz-border bg-xz-panel/50 p-3 text-[10px] leading-relaxed text-xz-ink-muted">
            <div className="mb-1 font-semibold text-[#D4B574]">用法</div>
            <ul className="list-disc pl-4 space-y-0.5">
              <li>点击卡片 → 查看角色资料 · 发消息 / 通话</li>
              <li>点「@ 提及」→ 输入框插入 @角色名</li>
              <li>消息里明确 @白前辈 / @北河 → 优先唤醒该角色</li>
            </ul>
          </div>
        </div>
      </aside>
    </>
  );
}
