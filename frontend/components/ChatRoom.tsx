"use client";

/** ChatRoom — main page composition. 九洲一号群「深墨金」主题重构
 *  Stage 8 UI 美化：
 *  - 顶部 Header：金色 logo + 九洲一号群成员 chip + 金线分隔
 *  - 中部 MessageList：TimeGroupDivider（金色字标）+ ChatBubble（深墨金气泡）
 *  - 底部 Composer：金色输入框 + 金色 send 按钮
 *  - 右侧 GroupSidebar：深墨金卡片
 *  - 左侧 ContactList：深墨金头像列表
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import ChatBubble from "@/components/ChatBubble";
import ChatInput from "@/components/ChatInput";
import ConnectionStatus from "@/components/ConnectionStatus";
import ContactList from "@/components/ContactList";
import DailyDaoYan from "@/components/DailyDaoYan";
import DMWindow from "@/components/DMWindow";
import GroupSidebar from "@/components/GroupSidebar";
import RoleProfile from "@/components/RoleProfile";
import TimeGroupDivider from "@/components/TimeGroupDivider";
import AdminSettingsModal from "@/components/AdminSettingsModal";
import DesktopTitleBar from "@/components/DesktopTitleBar";
import { defaultApiBase, ROLE_CYCLE, ROLE_META, type RoleKey } from "@/lib/ws";
import { ChatProvider, useChat } from "@/lib/ChatContext";
import type { ChatMessage } from "@/lib/ChatContext";

const TIME_GROUP_GAP_MS = 5 * 60 * 1000; // 5 分钟

function AgentChip({ k }: { k: RoleKey }) {
  const meta = ROLE_META[k];
  return (
    <div
      className="flex items-center gap-1.5 rounded-full border border-xz-border bg-xz-panel/70 px-2.5 py-1 text-[11px] shadow-sm backdrop-blur"
      title={meta.blurb}
      data-agent={k}
    >
      <span className="text-base leading-none">{meta.emoji}</span>
      <span className={`font-semibold ${meta.text}`}>{meta.name}</span>
      <span className="rounded bg-xz-bg-2 px-1 py-px text-[9px] font-medium text-xz-ink-muted ring-1 ring-xz-border-soft">
        {meta.realmShort}
      </span>
    </div>
  );
}

/** T9 / Piece B: 「我: 神秘人 ✎」 inline editor.
 *  - 点 ✎ 进入编辑模式 → input + Enter 保存 / Esc 取消
 *  - 默认 "神秘人"，可改为任意 ≤ 24 字 display name
 *  - 调用 ChatContext.setDisplayName → userIdentity hook → localStorage
 */
function UserBadge() {
  const { displayName, setDisplayName } = useChat();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName);

  // 切回非编辑态时把 draft 同步到当前 displayName (e.g. storage event)
  useEffect(() => {
    if (!editing) setDraft(displayName);
  }, [displayName, editing]);

  const handleSave = useCallback(() => {
    setDisplayName(draft);
    setEditing(false);
  }, [draft, setDisplayName]);

  const handleCancel = useCallback(() => {
    setDraft(displayName);
    setEditing(false);
  }, [displayName]);

  if (editing) {
    return (
      <div className="ml-1 flex items-center gap-1" data-testid="user-badge-editing">
        <span className="text-[11px] font-medium text-xz-ink-muted">我:</span>
        <input
          autoFocus
          data-testid="user-badge-input"
          value={draft}
          maxLength={24}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSave();
            } else if (e.key === "Escape") {
              e.preventDefault();
              handleCancel();
            }
          }}
          onBlur={handleSave}
          className="w-24 rounded-md border border-xz-gold/60 bg-xz-panel px-1.5 py-0.5 text-[11px] font-semibold text-xz-gold-bright shadow-inner outline-none focus:border-xz-gold focus:ring-1 focus:ring-xz-gold/40"
        />
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="ml-1 inline-flex items-center gap-1 rounded-full border border-xz-border bg-xz-panel/80 px-2.5 py-0.5 text-[11px] font-semibold text-xz-ink shadow-sm transition hover:border-xz-gold hover:bg-xz-panel-2 hover:text-xz-gold-bright active:scale-95"
      data-testid="user-badge"
      title="点击修改你的显示名"
      aria-label={`当前显示名 ${displayName}，点击修改`}
    >
      <span className="text-xz-ink-muted">我:</span>
      <span className="text-xz-gold-bright" data-testid="user-badge-name">{displayName}</span>
      <span aria-hidden className="text-[10px] text-xz-ink-muted">✎</span>
    </button>
  );
}

function RoomHeader({
  onOpenSidebar,
  onClearHistory,
  onOpenAdminSettings,
}: {
  onOpenSidebar: () => void;
  onClearHistory?: () => void;
  onOpenAdminSettings?: () => void;
}) {
  const { status, reconnect, topic, sessionId, agentKeys } = useChat();
  return (
    <header className="border-b border-xz-border bg-xz-bg/90 px-4 py-3 shadow-md shadow-black/30 backdrop-blur-xl sm:px-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          {/* 九洲一号群 logo — 金箔装饰 + 太极 */}
          <div className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-[#C7A969] via-[#D4B574] to-[#8E7847] text-base font-bold text-xz-bg shadow-inner ring-1 ring-[#D4B574]/60">
            <span className="font-xiuzhen-title text-lg text-xz-bg drop-shadow-sm">九</span>
            <span className="absolute -inset-px rounded-xl border border-[#C7A969]/30" aria-hidden />
          </div>
          <div>
            <h1 className="font-xiuzhen-title gold-text text-lg font-bold">
              九洲一号群
            </h1>
            <p className="text-xs text-xz-ink-muted" suppressHydrationWarning>
              {topic ? `${topic}` : `${sessionId} · 九洲一号群聊天群`}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <UserBadge />
          {(agentKeys.length > 0 ? agentKeys : ROLE_CYCLE).map((k) => (
            <AgentChip key={k} k={k as RoleKey} />
          ))}
          <ConnectionStatus
            status={status}
            onReconnect={reconnect}
            agentCount={agentKeys.length || ROLE_CYCLE.length}
          />
          {onOpenAdminSettings && (
            <button
              type="button"
              onClick={onOpenAdminSettings}
              className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-[#C7A969]/40 bg-[#C7A969]/10 px-3 py-1.5 text-xs font-semibold text-[#C7A969] shadow-sm transition hover:border-[#C7A969] hover:bg-[#C7A969]/20 active:scale-95"
              data-testid="open-admin-settings"
              title="配置 LLM provider / model / API key"
              aria-label="打开模型配置"
            >
              配置
            </button>
          )}
          <button
            type="button"
            onClick={onOpenSidebar}
            className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-xz-border bg-xz-panel/80 px-3 py-1.5 text-xs font-semibold text-xz-ink shadow-sm transition hover:border-xz-gold hover:bg-xz-panel-2 hover:text-xz-gold-bright active:scale-95"
            data-testid="open-sidebar"
            aria-label="打开九洲一号群成员列表"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            群友
          </button>
          {onClearHistory && (
            <button
              type="button"
              onClick={onClearHistory}
              className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-[#8B3A3A]/40 bg-[#8B3A3A]/10 px-3 py-1.5 text-xs font-semibold text-[#A84545] shadow-sm transition hover:border-[#8B3A3A] hover:bg-[#8B3A3A]/20 hover:text-[#C25555] active:scale-95"
              data-testid="clear-group-history"
              title="清除本群聊窗口的所有消息（其他窗口不受影响）"
              aria-label="清除本群聊窗口所有消息"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6" />
                <path d="M14 11v6" />
                <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
              </svg>
              清除
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

/** 把消息列表按时间分组：相邻间隔 > 5 分钟插入 TimeGroupDivider */
function useTimeGroupedMessages(messages: ChatMessage[]) {
  return useMemo(() => {
    type Item =
      | { kind: "bubble"; message: ChatMessage }
      | { kind: "divider"; ts: number };
    const out: Item[] = [];
    let lastTs: number | null = null;
    for (const m of messages) {
      const ts = m.timestamp;
      if (lastTs !== null && ts - lastTs > TIME_GROUP_GAP_MS) {
        out.push({ kind: "divider", ts: ts });
      }
      out.push({ kind: "bubble", message: m });
      lastTs = ts;
    }
    return out;
  }, [messages]);
}

function MessageList({
  onOpenProfile,
}: {
  onOpenProfile?: (role: RoleKey) => void;
}) {
  const { messages, status } = useChat();
  const items = useTimeGroupedMessages(messages);
  const isEmpty = messages.length === 0;
  return (
    <>
      {/* Stage 8-B 「灵韵」: 装饰 banner — 今日道言（位于 header 与消息流之间，host 外） */}
      <DailyDaoYan />
      {/* 固定舞台 host — 深墨金静底（无雨雪时段氛围） */}
      <div
        className="message-stage-host relative flex min-h-[200px] flex-1 flex-col overflow-hidden"
        data-testid="message-stage-host"
      >
        <div
          className="pointer-events-none absolute inset-0 z-0 chat-wallpaper"
          aria-hidden
          data-testid="stage-bg-layer"
        />
        <main className="relative z-10 flex-1 overflow-y-auto bg-transparent px-4 py-6 sm:px-6">
        <div
          className="mx-auto flex max-w-4xl flex-col gap-3"
          suppressHydrationWarning
        >
          {isEmpty && (
            <div className="mt-16 flex flex-col items-center gap-3 text-center">
              <div className="relative flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-[#C7A969] via-[#D4B574] to-[#8E7847] text-3xl ring-1 ring-[#D4B574]/40 shadow-lg shadow-[#C7A969]/20">
                <span className="font-xiuzhen-title text-2xl text-xz-bg">道</span>
                <span className="absolute -inset-px rounded-2xl border border-[#C7A969]/30" aria-hidden />
              </div>
              <div className="font-xiuzhen-title text-sm font-semibold text-xz-ink">
                {status === "connected"
                  ? "九洲一号群 6 友已就位 · 发个消息开始"
                  : "连接中…"}
              </div>
              <div className="text-xs text-xz-ink-muted">
                试试：&ldquo;@白前辈 最近可好&rdquo; · &ldquo;九洲一号群界大事&rdquo; · &ldquo;九幽冰莲哪儿找&rdquo;
              </div>
            </div>
          )}
          {items.map((item, i) => {
            if (item.kind === "divider") {
              return <TimeGroupDivider key={`div-${item.ts}-${i}`} ts={item.ts} />;
            }
            const m = item.message;
            if (m.role === "ai") {
              // Stage 8-B: 回复链 — 在 messages 数组中向前找最近的 AI 发言，
              // 若上一个 AI 是不同 NPC，则当前气泡是"回复" → 显示 @Xxx → 我
              let parentSpeakerKey: RoleKey | null = null;
              for (let j = i - 1; j >= 0; j--) {
                const prev = items[j];
                if (prev.kind === "bubble" && prev.message.role === "ai") {
                  if (
                    prev.message.agentKey &&
                    prev.message.agentKey !== m.agentKey
                  ) {
                    parentSpeakerKey = prev.message.agentKey;
                  }
                  break;
                }
              }
              return (
                <ChatBubble
                  key={m.id}
                  id={m.id}
                  role={m.role}
                  text={m.text}
                  isStreaming={m.isStreaming}
                  speakerName={m.speakerName}
                  speakerEmoji={m.speakerEmoji}
                  agentKey={m.agentKey}
                  timestamp={m.timestamp}
                  round={m.round}
                  parentSpeakerKey={parentSpeakerKey}
                  onAvatarClick={onOpenProfile}
                />
              );
            }
            if (m.role === "user") {
              // T9 / Piece B: 把 user 的 author 字段透传给 ChatBubble
              return (
                <ChatBubble
                  key={m.id}
                  id={m.id}
                  role={m.role}
                  text={m.text}
                  isStreaming={false}
                  timestamp={m.timestamp}
                  author={"author" in m ? m.author : undefined}
                />
              );
            }
            return (
              <ChatBubble
                key={m.id}
                id={m.id}
                role={m.role}
                text={m.text}
                isStreaming={false}
                timestamp={m.timestamp}
                status={"status" in m ? m.status : undefined}
              />
            );
          })}
          <div id="chat-bottom-anchor" />
        </div>
      </main>
      </div>
    </>
  );
}

function Composer({
  text,
  onChange,
  onSend,
  onOpenSidebar,
  disabled,
}: {
  text: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onOpenSidebar: () => void;
  disabled: boolean;
}) {
  const charCount = text.length;
  return (
    <footer className="shrink-0 border-t border-xz-border bg-[#1F1F1F] px-4 py-3 sm:px-6">
      <div className="mx-auto max-w-4xl">
        <div className="flex max-h-36 items-end gap-2">
          <button
            type="button"
            onClick={onOpenSidebar}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-xz-border bg-xz-panel text-xz-ink-muted shadow-sm transition hover:border-xz-gold hover:bg-xz-panel-2 hover:text-xz-gold-bright active:scale-95 sm:hidden"
            aria-label="群友列表"
            data-testid="open-sidebar-mobile"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </button>
          <div className="min-h-0 min-w-0 flex-1">
            <ChatInput
              value={text}
              onChange={onChange}
              onSend={onSend}
              disabled={disabled}
              placeholder={
                disabled
                  ? "等待 WebSocket 连接…"
                  : "发个消息试试 @白前辈 @药师…"
              }
            />
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-xz-ink-muted">
          <span>
            {disabled
              ? "若持续离线请点 Reconnect"
              : "Enter 发送 · Shift+Enter 换行 · 角色会回应，也可能保持沉默"}
          </span>
          <span className={charCount > 500 ? "font-semibold text-xz-cinnabar-bright" : ""}>
            {charCount} 字
          </span>
        </div>
      </div>
    </footer>
  );
}

/** ===== Stage 8: 群聊主区（保留所有原逻辑） ===== */
function GroupChatMain({
  text,
  setText,
  onSend,
  onOpenSidebar,
  onClearHistory,
  onOpenProfile,
  onOpenAdminSettings,
  disabled,
}: {
  text: string;
  setText: (v: string) => void;
  onSend: () => void;
  onOpenSidebar: () => void;
  onClearHistory: () => void;
  onOpenProfile?: (role: RoleKey) => void;
  onOpenAdminSettings?: () => void;
  disabled: boolean;
}) {
  return (
    <>
      <RoomHeader
        onOpenSidebar={onOpenSidebar}
        onClearHistory={onClearHistory}
        onOpenAdminSettings={onOpenAdminSettings}
      />
      <MessageList onOpenProfile={onOpenProfile} />
      <Composer
        text={text}
        onChange={setText}
        onSend={onSend}
        onOpenSidebar={onOpenSidebar}
        disabled={disabled}
      />
    </>
  );
}

/** ===== Stage 6 DM: 顶层容器（mode + dmTarget 切换 + text/sidebar 共享状态） ===== */
function ChatRoomInner() {
  // 群聊 mode / 私信 mode
  const [mode, setMode] = useState<"group" | "dm">("group");
  const [dmTarget, setDmTarget] = useState<RoleKey | null>(null);
  // 资料卡：仅群聊消息气泡头像打开；左侧 ContactList 直达 DM
  const [profileTarget, setProfileTarget] = useState<RoleKey | null>(null);
  // Stage 9: admin LLM settings modal
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  // group 模式下的右侧 GroupSidebar 控制权
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 群聊 composer 文本（提到 ChatRoomContainer 是因为 GroupSidebar.onPick 也要改它）
  const [text, setText] = useState("");
  // 群聊发送
  const { send, status, sessionId, reconnect, messages, setMessages } = useChat();
  const disabled = status !== "connected";
  const handleSend = useCallback(() => {
    if (disabled) return;
    send(text);
    setText("");
  }, [disabled, send, text]);

  /** 左侧联系人栏：群聊入口回群；角色 → 直接切换私聊窗口（不弹资料） */
  const handleSelectContact = useCallback((role: RoleKey | null) => {
    if (role === null) {
      setMode("group");
      setDmTarget(null);
      setProfileTarget(null);
    } else {
      setProfileTarget(null);
      setMode("dm");
      setDmTarget(role);
    }
  }, []);

  /** 群聊消息区头像：打开资料主页（非侧栏切换） */
  const handleOpenProfile = useCallback((role: RoleKey) => {
    setProfileTarget(role);
    setSidebarOpen(false);
  }, []);

  const handleCloseProfile = useCallback(() => {
    setProfileTarget(null);
  }, []);

  /** 资料卡「发消息」→ 进 DM + 关资料 */
  const handleProfileMessage = useCallback((role: RoleKey) => {
    setProfileTarget(null);
    setMode("dm");
    setDmTarget(role);
  }, []);

  const handleBackToGroup = useCallback(() => {
    setMode("group");
    setDmTarget(null);
  }, []);

  // @群友 「@ 提及」→ 自动插入到输入框（直接修改 text state）— 提到顶层共享
  const handlePickRole = useCallback((k: RoleKey) => {
    const meta = ROLE_META[k];
    setText((cur) => {
      const sep = cur.length > 0 && !cur.endsWith(" ") && !cur.endsWith("\n") ? " " : "";
      return cur + sep + "@" + meta.name + " ";
    });
    setSidebarOpen(false);
  }, []);

  /**
   * T9 / Piece C: 群聊 Clear 按钮 handler.
   *
   * Flow:
   *   1. window.confirm 防误触
   *   2. DELETE /api/group/history?session_id=<当前 sid> — 真删 SQLite (group 行, 跨 6 角色)
   *   3. 清 messages state (本地 UI)
   *   4. setSessionId(s-${newId()}) → 触发 reconnect({forceNewSession: true}) 流程,
   *      新 sid 持久化到 localStorage, 下次 reload 也用这个新 sid
   *
   * 关键: 不动 DM 行, 不影响其他 session, 不影响其他 tab。
   */
  const handleClearGroupHistory = useCallback(async () => {
    if (typeof window === "undefined") return;
    const ok = window.confirm("确定要清除本群聊窗口的所有消息吗?(其他窗口不受影响)");
    if (!ok) return;
    const baseHttp = defaultApiBase();
    try {
      const r = await fetch(
        `${baseHttp}/api/group/history?session_id=${encodeURIComponent(sessionId)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        // eslint-disable-next-line no-console
        console.error("[clear-group] DELETE failed:", r.status, await r.text());
        window.alert("清除失败, 请稍后再试");
        return;
      }
      const body = await r.json();
      // eslint-disable-next-line no-console
      console.info("[clear-group] deleted", body.deleted, "rows");
      // 清本地 messages + 新 sessionId → 触发 ChatContext useEffect rebuild socket
      setMessages([]);
      // 强制新 sid (reconnect({forceNewSession: true}) 路径)
      reconnect({ forceNewSession: true });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[clear-group] network error:", e);
      window.alert("清除失败: 网络异常");
    }
  }, [sessionId, reconnect, setMessages]);

  // 抑制未使用变量警告 — messages 在 handleClearGroupHistory 之后还会被引用, 留着便于调试
  void messages;

  return (
    <div
      className="flex h-screen w-screen flex-col overflow-hidden bg-xz-bg text-xz-ink"
      data-testid="chat-room"
      data-mode={mode}
      data-dm-target={dmTarget ?? ""}
    >
      <DesktopTitleBar />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <ContactList
          selected={dmTarget}
          onSelect={handleSelectContact}
          inGroupMode={mode === "group"}
        />

        <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
          {mode === "group" ? (
            <GroupChatMain
              text={text}
              setText={setText}
              onSend={handleSend}
              onOpenSidebar={() => setSidebarOpen(true)}
              onClearHistory={handleClearGroupHistory}
              onOpenProfile={handleOpenProfile}
              onOpenAdminSettings={() => setAdminModalOpen(true)}
              disabled={disabled}
            />
          ) : (
            <DMWindow
              target={dmTarget!}
              onBackToGroup={handleBackToGroup}
              onOpenProfile={handleOpenProfile}
            />
          )}
        </div>

        {mode === "group" && (
          <GroupSidebar
            open={sidebarOpen}
            onClose={() => setSidebarOpen(false)}
            onPick={handlePickRole}
          />
        )}

        {profileTarget && (
          <RoleProfile
            roleKey={profileTarget}
            onClose={handleCloseProfile}
            onMessage={handleProfileMessage}
          />
        )}

        <AdminSettingsModal
          isOpen={adminModalOpen}
          onClose={() => setAdminModalOpen(false)}
        />
      </div>
    </div>
  );
}

export default function ChatRoom() {
  return (
    <ChatProvider>
      <ChatRoomInner />
    </ChatProvider>
  );
}
