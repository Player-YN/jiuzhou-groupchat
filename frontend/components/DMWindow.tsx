"use client";

/** DMWindow — 私信聊天窗口 (深墨金主题, Stage 8)
 *
 * 架构:
 *  - useDmSession hook 维护独立 ChatSocket（独立 sessionId）
 *  - 首次握到 session_init 后立刻发 dm_init{target_agent}
 *  - dm_init 响应带回历史（持久化的 chat memory）
 *  - 用户输入 → dm_msg{text} → 流式 dm_thinking / dm_msg_chunk / dm_done
 *  - 失败 / 异常 → dm_error → UI 显示
 *  - 返回群聊 → 父组件 mode 切回 group，hook 的清理 effect 自动销毁 DM socket
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { defaultApiBase, ROLE_META, type RoleKey } from "@/lib/ws";
import {
  clearDmSessionId,
  useDmSession,
  type DmChatMessage,
} from "@/lib/useDmSession";
import { getDisplayName, useUserIdentity } from "@/lib/userIdentity";
import AgentAvatar from "./AgentAvatar";
import ChatBubble from "./ChatBubble";


type Props = {
  target: RoleKey;
  onBackToGroup: () => void;
  /** Stage 10: AI 头像点击打开资料 */
  onOpenProfile?: (role: RoleKey) => void;
};

function fmtTime(ts?: number) {
  if (!ts) return "";
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}:${d
    .getSeconds()
    .toString()
    .padStart(2, "0")}`;
}

function StatusBar({ status }: { status: ReturnType<typeof useDmSession>["status"] }) {
  if (status === "connected") return null;
  if (status === "connecting") {
    return (
      <div className="mx-auto mt-2 flex max-w-2xl items-center gap-2 rounded-lg border border-xz-border bg-xz-panel/80 px-3 py-1.5 text-[11px] text-[#D4B574]">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#C7A969]" />
        连接中…
      </div>
    );
  }
  if (status === "reconnecting") {
    return (
      <div className="mx-auto mt-2 flex max-w-2xl items-center gap-2 rounded-lg border border-xz-border bg-xz-panel/80 px-3 py-1.5 text-[11px] text-[#C7A969]">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#C7A969]" />
        重连中…
      </div>
    );
  }
  if (status === "error" || status === "disconnected") {
    return (
      <div className="mx-auto mt-2 flex max-w-2xl items-center gap-2 rounded-lg border border-[#8B3A3A]/50 bg-[#8B3A3A]/20 px-3 py-1.5 text-[11px] text-[#A84545]">
        <span className="h-1.5 w-1.5 rounded-full bg-[#8B3A3A]" />
        连接断开
      </div>
    );
  }
  return null;
}

function MessageStream({
  messages,
  target,
  targetName,
  onOpenProfile,
}: {
  messages: DmChatMessage[];
  target: RoleKey;
  targetName: string;
  onOpenProfile?: (role: RoleKey) => void;
}) {
  // 自动滚到底
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, 30);
    return () => clearTimeout(t);
  }, [messages.length]);

  const meta = ROLE_META[target];

  if (messages.length === 0) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col items-center justify-center gap-4 py-16 text-center">
        <div className="relative flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-xz-panel to-xz-panel-2 text-4xl ring-1 ring-xz-border shadow-md shadow-black/50">
          <span>{meta.emoji}</span>
          <span className="absolute -inset-px rounded-3xl border border-xz-border" aria-hidden />
        </div>
        <div className="space-y-1.5">
          <h3 className="font-xiuzhen-title text-base font-semibold text-xz-ink">
            还没和 <span className={meta.text}>{targetName}</span> 说过话
          </h3>
          <p className="max-w-md text-xs leading-relaxed text-xz-ink-muted">
            在下方输入第一条私信开始对话，<span className={`font-semibold ${meta.text}`}>{targetName}</span>{" "}
            在自己的<b className="text-[#D4B574]">独立记忆库</b>中保留上下文，<span className="font-medium text-[#A84545]">其他 AI 不会看到这条消息</span>。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4 py-2" data-testid="dm-message-stream">
      {messages.map((m) => {
        if (m.kind === "user") {
          // T9 / Piece B: DM user bubble 也显示 author
          return (
            <ChatBubble
              key={m.id}
              id={m.id}
              role="user"
              text={m.text}
              isStreaming={false}
              timestamp={m.timestamp}
              status={m.status}
              author={m.author}
              size="dm"
            />
          );
        }
        if (m.kind === "agent") {
          return (
            <ChatBubble
              key={m.id}
              id={m.id}
              role="ai"
              text={m.text}
              isStreaming={m.isStreaming}
              speakerName={m.agentName}
              speakerEmoji={m.agentEmoji}
              agentKey={m.agentKey}
              timestamp={m.timestamp}
              source={m.source ?? "dm"}
              size="dm"
              onAvatarClick={onOpenProfile}
            />
          );
        }
        // system pill
        return (
          <ChatBubble
            key={m.id}
            id={m.id}
            role="system"
            text={m.text}
            isStreaming={false}
            timestamp={m.timestamp}
            status={m.status}
            size="dm"
          />
        );
      })}
      <div ref={endRef} id="dm-bottom-anchor" />
    </div>
  );
}

export default function DMWindow({ target, onBackToGroup, onOpenProfile }: Props) {
  const meta = ROLE_META[target];
  const { status, init, messages, send, reconnect, sessionId, clearLocalHistory } =
    useDmSession(target);
  const [text, setText] = useState("");


  // init.ready 后：用 server 推回的 name/emoji（万一未来 ROLE_META 跟 backend 不一致也不会乱）
  const displayName = init.phase === "ready" ? init.name : meta.name;
  const displayEmoji = init.phase === "ready" ? init.emoji : meta.emoji;
  const memorySize = init.phase === "ready" ? init.memorySize : 0;

  /**
   * T9 / Piece C: DM Clear 按钮 handler.
   *
   * Flow:
   *   1. confirm 防误触
   *   2. DELETE /api/dm/history?session_id=<sid>&agent_key=<target>
   *      → 后端只删 (sid, target, source='dm') 行, 不影响群聊 + 不影响其他 target
   *   3. clearLocalHistory() — 清 messages + 强制新 sid
   *   4. clearDmSessionId(target) — 清 localStorage, 下次 reload 不恢复旧 sid
   *
   * 注意: 不调用 reconnect(), 而是 clearLocalHistory() — 后者做的是
   * forceNewSession 路径, 并立即清 messages.
   */
  const handleClearDmHistory = useCallback(async () => {
    if (typeof window === "undefined") return;
    const ok = window.confirm(
      `确定要清除与 ${displayName} 的所有私信吗?(其他 AI 的 DM 不受影响)`,
    );
    if (!ok) return;
    const baseHttp = defaultApiBase();
    try {
      const r = await fetch(
        `${baseHttp}/api/dm/history?session_id=${encodeURIComponent(sessionId)}&agent_key=${encodeURIComponent(target)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        // eslint-disable-next-line no-console
        console.error("[clear-dm] DELETE failed:", r.status, await r.text());
        window.alert("清除失败, 请稍后再试");
        return;
      }
      const body = await r.json();
      // eslint-disable-next-line no-console
      console.info("[clear-dm] deleted", body.deleted, "rows");
      // 清 localStorage + 本地 UI + 新 sid
      clearDmSessionId(target);
      clearLocalHistory();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[clear-dm] network error:", e);
      window.alert("清除失败: 网络异常");
    }
  }, [sessionId, target, displayName, clearLocalHistory]);

  const streamActive = messages.some((m) => m.kind === "agent" && m.isStreaming);
  const disabled =
    status !== "connected" || init.phase !== "ready" || streamActive;

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (disabled) return;
    const ok = send(trimmed);
    if (ok) setText("");
  }, [disabled, send, text]);

  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // 用 useMemo 避免 MessageStream 重渲染（虽然 React 已经 memo 内部）
  const memoedMessages = useMemo(() => messages, [messages]);

  // 仅在客户端 mount 后才设置当前时间显示，避免 SSR/CSR hydration 不一致
  const [clientNow, setClientNow] = useState<number | null>(null);
  useEffect(() => {
    setClientNow(Date.now());
    const t = setInterval(() => setClientNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div
      className="relative flex h-full w-full flex-col overflow-hidden bg-xz-bg"
      data-testid="dm-window"
      data-target={target}
    >
      {/* ===== Header — 深墨金 ===== */}
      <header className="border-b border-xz-border bg-xz-bg/90 shadow-md shadow-black/40 backdrop-blur-xl">
        <div className="flex items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onBackToGroup}
              data-testid="dm-back"
              className="flex h-8 w-8 items-center justify-center rounded-full border border-xz-border bg-xz-panel/80 text-xz-ink-muted transition hover:border-[#C7A969] hover:bg-xz-panel-2 hover:text-[#D4B574] active:scale-95"
              title="返回群聊"
              aria-label="返回群聊"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
            {/* Stage 8-B: 头像更大 (lg, h-12 w-12)，亲密化；Stage 10 可点开资料 */}
            <AgentAvatar
              agentKey={target}
              size="lg"
              ring
              showRealmTag
              onClick={onOpenProfile ? () => onOpenProfile(target) : undefined}
            />
            <div>
              <div className="flex items-center gap-1.5">
                <h2 className={`font-xiuzhen-title text-base font-semibold ${meta.text}`}>
                  私聊 · {displayName}
                </h2>
                <span
                  className={`h-1.5 w-1.5 rounded-full ${meta.dot} ring-2 ring-xz-bg`}
                />
                <span className="text-[10px] font-medium text-[#7A9387]">在线</span>
                <span
                  className={`text-base leading-none ${init.phase === "ready" ? "" : "opacity-40"}`}
                  aria-hidden
                >
                  {displayEmoji}
                </span>
              </div>
              <p className="text-[11px] text-xz-ink-muted">
                {meta.realm} · {meta.blurb} · 仅 ta 可见
                <span className="mx-1.5 inline-block h-1 w-1 -translate-y-px rounded-full bg-xz-border" />
                <span data-testid="dm-memory-size">
                  {init.phase === "ready"
                    ? `记忆 ${memorySize} 条`
                    : init.phase === "loading"
                    ? "加载中…"
                    : "握手失败"}
                </span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* T9 / Piece B: 「我: 凡人 ✎」inline editor — DM header 也展示 */}
            <DmUserBadge />
            {status !== "connected" && (
              <button
                type="button"
onClick={() => reconnect()}
                className="rounded-full border border-xz-border bg-xz-panel/80 px-2.5 py-0.5 text-[10px] font-semibold text-xz-ink-muted transition hover:border-[#C7A969] hover:text-[#D4B574] active:scale-95"
                data-testid="dm-reconnect"
              >
                重连
              </button>
            )}
            {/* T9 / Piece C: Clear 按钮 — 清空当前 DM target 的所有消息 */}
            <button
              type="button"
              onClick={handleClearDmHistory}
              className="inline-flex items-center gap-1 rounded-full border border-[#8B3A3A]/40 bg-[#8B3A3A]/10 px-2.5 py-0.5 text-[10px] font-semibold text-[#A84545] shadow-sm transition hover:border-[#8B3A3A] hover:bg-[#8B3A3A]/20 hover:text-[#C25555] active:scale-95"
              data-testid="clear-dm-history"
              title={`清除与 ${displayName} 的所有私信（其他 AI 的 DM 不受影响）`}
              aria-label={`清除与 ${displayName} 的所有私信`}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6" />
                <path d="M14 11v6" />
              </svg>
              清除
            </button>
            <div
              className="rounded-full bg-[#5C7367]/25 px-2.5 py-0.5 text-[10px] font-semibold text-[#7A9387] ring-1 ring-[#5C7367]/40"
              title="该 AI 拥有独立记忆库，与群聊隔离"
              data-testid="dm-isolation-badge"
            >
              独立记忆
            </div>
          </div>
        </div>
        {/* Stage 8-B: header 下方加 1px 金线渐变 + 呼吸光 (4s loop, opacity 0.4 ↔ 0.6) */}
        <div
          aria-hidden
          className="h-px w-full animate-goldBreath"
          data-testid="dm-header-gold-line"
          style={{
            backgroundImage:
              "linear-gradient(90deg, transparent 0%, rgba(199, 169, 105, 0.55) 30%, rgba(212, 181, 116, 0.85) 50%, rgba(199, 169, 105, 0.55) 70%, transparent 100%)",
          }}
        />
      </header>

      <StatusBar status={status} />

      {/* PR1: 固定舞台 host — 背景不随消息滚 */}
      <div
        className="message-stage-host relative flex min-h-[200px] flex-1 flex-col overflow-hidden"
        data-testid="dm-message-stage-host"
      >
        <div
          className="pointer-events-none absolute inset-0 z-0 chat-wallpaper"
          aria-hidden
          data-testid="dm-stage-bg-layer"
        />
        <main className="relative z-10 flex-1 overflow-y-auto bg-transparent px-6 py-6 sm:px-10">
        {/* 握手失败时展示明确错误 */}
        {init.phase === "failed" ? (
          <div className="mx-auto mt-8 flex max-w-md flex-col gap-3 rounded-2xl border border-[#8B3A3A]/50 bg-[#8B3A3A]/15 p-5 text-center shadow-sm">
            <div className="text-3xl">⚠️</div>
            <h3 className="text-sm font-semibold text-[#A84545]">
              私信握手失败：{init.code}
            </h3>
            <p className="text-xs text-[#A84545]/80">{init.message}</p>
            <button
              type="button"
              onClick={() => reconnect()}
              className="self-center rounded-full bg-[#8B3A3A] px-4 py-1.5 text-xs font-semibold text-xz-ink shadow-sm transition hover:bg-[#A84545] active:scale-95"
            >
              重新连接
            </button>
          </div>
        ) : (
          <MessageStream
            messages={memoedMessages}
            target={target}
            targetName={displayName}
            onOpenProfile={onOpenProfile}
          />
        )}
      </main>
      </div>

      {/* ===== Footer 输入框（active） ===== */}
      <footer className="shrink-0 border-t border-xz-border bg-[#1F1F1F] px-4 py-3 sm:px-6">
        <div className="mx-auto max-w-2xl">
          <div className="flex max-h-36 items-end gap-2">
            <textarea
              data-testid="dm-input"
              rows={1}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                const ta = e.target;
                ta.style.height = "auto";
                ta.style.height = `${Math.min(ta.scrollHeight, 4 * 24)}px`;
              }}
              onKeyDown={handleKey}
              placeholder={
                init.phase === "ready"
                  ? `给 ${displayName} 发私信…`
                  : init.phase === "loading"
                  ? "握手中…"
                  : `连接异常 · 无法发送`
              }
              disabled={disabled}
              className="max-h-24 min-h-[40px] flex-1 resize-none overflow-y-auto rounded-2xl border border-xz-border bg-[#2A2620] px-4 py-2.5 text-sm text-xz-ink placeholder:text-xz-ink-dim focus:border-[#C7A969] focus:outline-none focus:ring-2 focus:ring-[#C7A969]/30 disabled:cursor-not-allowed disabled:bg-xz-bg-2 disabled:text-xz-ink-dim font-xiuzhen-body"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={disabled || !text.trim()}
              data-testid="dm-send"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#C7A969] to-[#D4B574] text-xz-bg shadow-sm shadow-[#C7A969]/30 transition hover:from-[#D4B574] hover:to-[#E0C58A] active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
              aria-label="发送私信"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m22 2-7 20-4-9-9-4 20-7Z" />
                <path d="M22 2 11 13" />
              </svg>
            </button>
          </div>
          <div className="mt-2 flex items-center justify-between text-[10px] text-xz-ink-muted">
            <span>
              {streamActive
                ? "对方正在输入回复…"
                : disabled
                ? init.phase === "failed"
                  ? "握手失败 · 请重新连接"
                  : "等待 WebSocket 握手…"
                : "Enter 发送 · Shift+Enter 换行"}
            </span>
            <span
              className={
                text.length > 500 ? "font-semibold text-[#C7A969]" : ""
              }
              data-testid="dm-char-count"
            >
              {text.length} 字
            </span>
            <span className="ml-3 hidden sm:inline">
              DM · 仅 {displayName} 可见 ·{" "}
              <span className="font-mono text-xz-ink-dim" suppressHydrationWarning>
              {clientNow ? fmtTime(clientNow) : ""}
            </span>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}

/** T9 / Piece B: DM 头部 inline display name 编辑器 (与 ChatRoom.UserBadge 同构).
 *  - 点 ✎ → input + Enter 保存 / Esc 取消 / blur 也保存
 *  - 共享同一个 userIdentity localStorage key, 跨群聊/DM 一致
 */
function DmUserBadge() {
  const { displayName, setDisplayName } = useUserIdentity();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName);

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
      <div className="flex items-center gap-1" data-testid="dm-user-badge-editing">
        <span className="text-[10px] font-medium text-xz-ink-muted">我:</span>
        <input
          autoFocus
          data-testid="dm-user-badge-input"
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
          className="w-24 rounded-md border border-[#C7A969]/60 bg-xz-panel px-1.5 py-0.5 text-[10px] font-semibold text-[#D4B574] shadow-inner outline-none focus:border-[#C7A969] focus:ring-1 focus:ring-[#C7A969]/40"
        />
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="inline-flex items-center gap-1 rounded-full border border-xz-border bg-xz-panel/80 px-2 py-0.5 text-[10px] font-semibold text-xz-ink shadow-sm transition hover:border-[#C7A969] hover:bg-xz-panel-2 hover:text-[#D4B574] active:scale-95"
      data-testid="dm-user-badge"
      title="点击修改你的显示名"
      aria-label={`当前显示名 ${displayName}，点击修改`}
    >
      <span className="text-xz-ink-muted">我:</span>
      <span className="text-[#D4B574]" data-testid="dm-user-badge-name">{displayName}</span>
      <span aria-hidden className="text-xz-ink-muted">✎</span>
    </button>
  );
}
