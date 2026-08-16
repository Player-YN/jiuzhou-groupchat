"use client";

/** ContactList — 左侧好友列表 (深墨金主题, Stage 8 / Stage 10)
 *
 * - 6 九洲一号群角色头像 + 名字 + 在线状态
 * - 点击角色 → onSelect(roleKey)：切换到该角色私聊窗口（不弹资料）
 * - 点击「群聊」→ onSelect(null) 回到群聊
 * - 高亮当前选中的 dmTarget
 * - 设计要点：
 *  - 宽度固定 112px（w-28，Stage 10 加厚 3D 左轨）
 *  - 头像为主，名字作为小字 label
 *  - 未读消息朱砂红点（unreadCount）
 */
import { ROLE_META, ROLE_CYCLE, type RoleKey } from "@/lib/ws";
import AgentAvatar from "./AgentAvatar";

type Props = {
  /** 当前选中的 DM 目标（null 表示群聊模式） */
  selected: RoleKey | null;
  /** null = 群聊；RoleKey = 切换到该角色 DM */
  onSelect: (role: RoleKey | null) => void;
  /** 未读消息数（按角色 key 索引）；可选，未提供就不显示红点 */
  unread?: Partial<Record<RoleKey, number>>;
  /** 当前是否处于群聊模式（用于高亮"群聊"入口） */
  inGroupMode?: boolean;
};

export default function ContactList({
  selected,
  onSelect,
  unread,
  inGroupMode = false,
}: Props) {
  return (
    <nav
      className={[
        "flex h-full w-28 shrink-0 flex-col items-center gap-2.5",
        "border-r border-[#C7A969]/20",
        "bg-gradient-to-b from-xz-bg via-xz-bg-2 to-xz-bg",
        "px-2 py-3",
        "shadow-[6px_0_28px_rgba(0,0,0,0.55)]",
      ].join(" ")}
      aria-label="联系人列表"
      data-testid="contact-list"
    >
      {/* ===== 群聊入口（顶部） ===== */}
      <button
        type="button"
        onClick={() => onSelect(null)}
        data-testid="contact-group"
        data-mode="group"
        title="九洲一号群 · 群聊"
        aria-label="群聊"
        aria-pressed={inGroupMode}
        className={[
          "group relative flex h-16 w-16 flex-col items-center justify-center rounded-2xl",
          "transition-all duration-200 active:scale-95",
          inGroupMode
            ? "bg-gradient-to-br from-[#C7A969] via-[#D4B574] to-[#8E7847] shadow-md shadow-[#C7A969]/30 ring-2 ring-[#D4B574]/60"
            : "bg-xz-panel/80 hover:bg-xz-panel-2 hover:shadow-sm ring-1 ring-xz-border",
        ].join(" ")}
      >
        <span className="font-xiuzhen-title text-xl text-xz-bg">九</span>
        <span
          className={[
            "mt-0.5 text-[10px] font-semibold",
            inGroupMode ? "text-xz-bg" : "text-xz-ink-muted",
          ].join(" ")}
        >
          群聊
        </span>
        {inGroupMode && (
          <span
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-[#C7A969] ring-2 ring-xz-bg"
            aria-hidden
          />
        )}
      </button>

      {/* 分隔 — 金线 */}
      <div className="my-1 h-px w-12 ink-divider" aria-hidden />

      {/* ===== 6 角色头像 ===== */}
      <ul className="flex flex-col items-center gap-3">
        {ROLE_CYCLE.map((k) => {
          const meta = ROLE_META[k];
          const isSelected = selected === k;
          const unreadN = unread?.[k] ?? 0;
          return (
            <li key={k}>
              <button
                type="button"
                onClick={() => onSelect(k)}
                data-testid={`contact-${k}`}
                data-role={k}
                title={`${meta.name} · ${meta.realm} · 私聊`}
                aria-label={`与 ${meta.name} 私聊`}
                aria-pressed={isSelected}
                className={[
                  "group relative flex flex-col items-center justify-center rounded-xl p-1.5",
                  "transition-all duration-200 active:scale-95",
                  isSelected
                    ? "bg-xz-panel-2 shadow-md ring-2 ring-offset-1 ring-offset-xz-bg"
                    : "hover:bg-xz-panel/70 hover:shadow-sm",
                ].join(" ")}
                style={
                  isSelected
                    ? ({
                        boxShadow: `0 4px 14px ${meta.accentHex}33, 0 0 0 2px ${meta.accentHex}55`,
                      } as React.CSSProperties)
                    : undefined
                }
              >
                <AgentAvatar agentKey={k} size="md" showRealmTag={false} />
                <span
                  className={[
                    "mt-1 max-w-[72px] truncate text-[10px] font-medium",
                    isSelected ? meta.text : "text-xz-ink-muted",
                  ].join(" ")}
                >
                  {meta.name}
                </span>
                {/* 在线小点 — 远山青 */}
                <span
                  className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#5C7367] ring-2 ring-xz-bg"
                  aria-hidden
                />
                {/* 未读红点 — 朱砂 */}
                {unreadN > 0 && (
                  <span
                    className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-[#8B3A3A] px-1 text-[9px] font-bold leading-none text-xz-ink ring-2 ring-xz-bg"
                    data-testid={`unread-${k}`}
                  >
                    {unreadN > 99 ? "99+" : unreadN}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      {/* 底部 footer（占位：可放"我"或设置入口） */}
      <div className="mt-auto flex flex-col items-center gap-1.5">
        <button
          type="button"
          title="我的（占位）"
          aria-label="我的"
          className="flex h-10 w-10 items-center justify-center rounded-full border border-xz-border bg-xz-panel/80 text-sm shadow-sm transition hover:border-[#C7A969] hover:shadow-md"
        >
          👤
        </button>
      </div>
    </nav>
  );
}
