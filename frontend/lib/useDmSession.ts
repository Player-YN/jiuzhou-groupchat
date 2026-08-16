"use client";

/** useDmSession — 单 Agent 私信会话状态机 (Stage 6 DM Phase 2 frontend)
 *
 * 设计要点：
 *  - 与群聊 ChatContext 共存：用**独立**的 ChatSocket 连接（独立 sessionId），
 *    让 DM 流不污染 group 流；这是 backend 协议"互斥但同一 WS 连接"约束的简化处理
 *    —— backend 注释说 "切换模式 (DM ↔ 群聊) 需要重连 WS"，前端开第二连接更稳。
 *  - 状态：
 *      status: WS 连接状态
 *      init: 拿到 dm_init 响应后才算 "会话就绪"；之前是 "loading" / "error"
 *      messages: 当前 DM 的消息列表（运行时聚合 history + 实时流）
 *      targetName / targetEmoji / targetAgentKey: 目标 AI 元数据
 *      streamingId: 当前正在流的 AI message id（chunk 拼接到它上面）
 *  - 交互：
 *      send(text): 先乐观 echo user 消息 + 触发服务端流；服务端 dm_msg_ack 后由 user 消息上线
 *      interrupt(): 触发服务端 dm_interrupt（当前后端只是 ack，不真打断）
 *
 * 错误处理：
 *  - WS 没连上 / 断了 → status = disconnected / reconnecting；UI 显示 banner
 *  - dm_error（target 非法、未 dm_init、stream 异常、持久化失败）→ 在 messages 末尾插 system pill
 *  - init 失败 → 在 UI 显示 "INIT_FAILED: code"，不进入会话
 *
 * Bug 1 修复 (2026-07-02)：身份绑定语义 — 同 ChatContext.tsx
 *  - dm_thinking 一次性绑定身份 (用 ev.payload.agent → resolveRole → ROLE_META)
 *  - 后续 dm_msg_chunk 只追加文本, 不再 resolveRole / 覆写身份
 *  - dm_done 只补全 name/emoji (若 server 提供), agentKey 沿用绑定值
 *  - RACE chunk 兜底: 用 chunk.agent 合成 bubble 并绑定
 *  - history 渲染: 用新 AgentMemoryEntry 字段 speaker_key (实际发言者) + source (group/dm)
 *    旧 DmMessageWire fallback (兼容未升级 backend)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ChatSocket,
  defaultSocketURL,
  ROLE_META,
  resolveRole,
  type AgentMemoryEntry,
  type ConnStatus,
  type DmMessageWire,
  type RoleKey,
  type ServerEvent,
} from "@/lib/ws";

import { getDisplayName } from "@/lib/userIdentity";

// ===== 前端内部统一的 message shape =====
export type DmChatMessage =
  | {
      id: string;
      kind: "user";
      text: string;
      timestamp: number;
      status: "sent" | "ack" | "fail";
      /** T9 / Piece B: 人类用户的署名 (默认 "神秘人")。 */
      author?: string;
    }
  | {
      id: string;
      kind: "agent";
      text: string;
      timestamp: number;
      isStreaming: boolean;
      agentKey: RoleKey | null;
      agentName: string;
      agentEmoji: string;
      /** 来源场景 (group / dm) — Bug 2 升级后前端 UI 显示暗色徽章
       *  缺省视为 dm (历史条目默认就是 dm 场景)
       */
      source?: "group" | "dm";
    }
  | { id: string; kind: "system"; text: string; timestamp: number; status?: string };

export type DmInitState =
  | { phase: "loading" }
  | { phase: "ready"; targetAgent: RoleKey | string; name: string; emoji: string; memorySize: number }
  | { phase: "failed"; code: string; message: string };

type DmContextValue = {
  status: ConnStatus;
  init: DmInitState;
  messages: DmChatMessage[];
  /** Local "send" — returns true iff message accepted (WS open). */
  send: (text: string) => boolean;
  interrupt: () => void;
  /**
   * Manual reconnect.
   *
   * T9 (Stage 8+ session fix): by default, reconnect() reuses the persisted
   * sessionId (same as on mount). It only rebuilds the WebSocket — history
   * is NOT lost. Pass `{ forceNewSession: true }` to explicitly start a fresh
   * conversation (e.g. via the "Clear" button, see Piece C).
   */
  reconnect: (opts?: { forceNewSession?: boolean }) => void;
  /** Current DM session id (read-only; mutate via reconnect). */
  sessionId: string;
  /** T9 / Piece C: clear local messages + force a new sid (NOT touching backend). */
  clearLocalHistory: () => void;
};

function newId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Per-target localStorage key. Each DM target gets its own slot — opening a
 *  DM with `shu-hang` doesn't disturb the `ling-die` DM, even across page
 *  reloads.
 *
 *  T9 (Stage 8+ session fix): replaced module-level `SESSION_ID_CACHE: Map`
 *  because Map state evaporates on (a) Fast Refresh in dev mode, (b) HMR
 *  rebuild, (c) StrictMode double-mount, (d) any code split that resets the
 *  module registry. Persisting to `localStorage` survives all of those and
 *  gives WeChat-style "same tab → same DM history" behavior.
 */
function dmStorageKey(target: RoleKey): string {
  return `dm-session-id:${target}`;
}

function readPersistedDmSessionId(target: RoleKey): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(dmStorageKey(target));
    if (v && typeof v === "string" && v.length > 0) return v;
  } catch {
    // localStorage may be blocked (privacy mode / quota); fall through
  }
  return null;
}

function writePersistedDmSessionId(target: RoleKey, sid: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(dmStorageKey(target), sid);
  } catch {
    // ignore quota / privacy errors; not fatal — next reload will mint new sid
  }
}

/** Get the persisted DM sessionId for `target`, minting & persisting a new one
 *  if none exists. Pure — does NOT mutate any module-level state, so it's safe
 *  to call from useState initializers and useEffect bodies.
 */
function getOrCreateDmSessionId(target: RoleKey): string {
  const persisted = readPersistedDmSessionId(target);
  if (persisted) return persisted;
  const fresh = `dm-${target}-${newId()}`;
  writePersistedDmSessionId(target, fresh);
  return fresh;
}

/** T9 / Piece C: explicitly wipe the persisted DM sid for `target`.
 *  Used by the Clear button to force a fresh conversation next mount.
 */
export function clearDmSessionId(target: RoleKey): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(dmStorageKey(target));
  } catch {
    // ignore
  }
}

/** Bug 1: 一次性身份绑定 —— 拿到 agent 字符串后, 用 ROLE_META 推导一致的
 *  (agentKey, agentName, agentEmoji)。若 resolveRole 失败, 返回 null agentKey +
 *  空字符串 name (后续渲染层 fallback 到中性 AI 主题)。
 *  这就是"绑定" —— 同一个 streaming bubble 内, 后续 chunk 沿用此结果, 不会再调用 resolveRole。
 */
function bindIdentity(agent: string | undefined): {
  agentKey: RoleKey | null;
  agentName: string;
  agentEmoji: string;
} {
  const k = resolveRole(agent);
  if (k && ROLE_META[k]) {
    return {
      agentKey: k,
      agentName: ROLE_META[k].name,
      agentEmoji: ROLE_META[k].emoji,
    };
  }
  // 不可识别 — 不假装是某角色
  return { agentKey: null, agentName: "", agentEmoji: "\u2728" };
}

/** 判断 entry 是否为新 schema (AgentMemoryEntry, 含 source + speaker_key) */
function isAgentMemoryEntry(e: unknown): e is AgentMemoryEntry {
  return (
    typeof e === "object" &&
    e !== null &&
    typeof (e as { source?: unknown }).source === "string" &&
    typeof (e as { speaker_key?: unknown }).speaker_key === "string"
  );
}

/** 把 backend history entry 转成前端的 DmChatMessage
 *  - 新 schema (AgentMemoryEntry): 用 speaker_key 决定 role, source 进 source 字段
 *  - 旧 schema (DmMessageWire): 用 role 字段, source 缺省视为 dm
 *  - speaker_key="user" 时渲染为 user bubble (kind=user), 不管 entry.role 是什么
 */
function historyEntryToMsg(
  e: AgentMemoryEntry | DmMessageWire,
  idx: number
): DmChatMessage {
  const id = `hist-${idx}-${e.timestamp}-${Math.random().toString(36).slice(2, 5)}`;
  const source: "group" | "dm" | undefined = isAgentMemoryEntry(e) ? e.source : undefined;

  if (isAgentMemoryEntry(e)) {
    // 新 schema — speaker_key 是 source of truth
    if (e.speaker_key === "user" || e.role === "user") {
      return {
        id,
        kind: "user",
        text: e.text,
        timestamp: e.timestamp,
        status: "ack",
        // T9 / Piece B: 从 backend 拉回的 author (default "神秘人" if null).
        // 旧 rows 的 author 字段可能不存在, 走 fallback.
        author: (e as AgentMemoryEntry & { author?: string | null }).author ?? "神秘人",
      };
    }
    // speaker_key 是 6 角色之一 — 用它绑定身份 (避免回退到 ROLE_META 第一个)
    const bound = bindIdentity(e.speaker_key);
    return {
      id,
      kind: "agent",
      text: e.text,
      timestamp: e.timestamp,
      isStreaming: false,
      agentKey: bound.agentKey,
      agentName: e.agent_name ?? bound.agentName,
      agentEmoji: e.agent_emoji ?? bound.agentEmoji,
      source,
    };
  }
  // 旧 schema fallback
  if (e.role === "user") {
    return { id, kind: "user", text: e.text, timestamp: e.timestamp, status: "ack" };
  }
  const bound = bindIdentity(e.agent_key ?? undefined);
  return {
    id,
    kind: "agent",
    text: e.text,
    timestamp: e.timestamp,
    isStreaming: false,
    agentKey: bound.agentKey,
    agentName: e.agent_name ?? bound.agentName,
    agentEmoji: e.agent_emoji ?? bound.agentEmoji,
    source, // undefined for old schema → 渲染层 fallback to "dm"
  };
}

export function useDmSession(target: RoleKey): DmContextValue {
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  const [init, setInit] = useState<DmInitState>({ phase: "loading" });
  const [messages, setMessages] = useState<DmChatMessage[]>([]);

  // 每个 DM target 用独立 sessionId（不同 role 之间不串），同一 target 在同一 tab 内
  // 复用同一 sessionId（localStorage-backed，见 getOrCreateDmSessionId 注释）
  // — 这样"切群聊再切回 DM"时还能加载历史。
  const [sessionId, setSessionId] = useState<string>(() => getOrCreateDmSessionId(target));

  // T9: mirror sessionId changes (e.g. after reconnect({forceNewSession: true})
  // or clearLocalHistory) back into localStorage so the next reload / DM
  // remount sees the fresh sid instead of minting yet another one.
  useEffect(() => {
    writePersistedDmSessionId(target, sessionId);
  }, [target, sessionId]);

  const socketRef = useRef<ChatSocket | null>(null);
  const streamingIdRef = useRef<string | null>(null);
  // 把目标 target 透传进 effect 但避免重启；保留 ref 让 send 拿到最新值
  const targetRef = useRef<RoleKey>(target);
  useEffect(() => {
    targetRef.current = target;
  }, [target]);

  // 主 effect：随 target/sessionId 重建 socket
  useEffect(() => {
    // 切换 target 时一切归零
    setMessages([]);
    setInit({ phase: "loading" });
    streamingIdRef.current = null;

    const url = defaultSocketURL(sessionId);
    const sock = new ChatSocket(url);
    socketRef.current = sock;

    const offStatus = sock.onStatus((s) => setStatus(s));

    let didInit = false;

    const offEvent = sock.onEvent((ev: ServerEvent) => {
      const ts = ev.ts;
      switch (ev.type) {
        case "session_init": {
          // WS 握手成功 → 立刻发 dm_init{target_agent}
          if (didInit) break;
          didInit = true;
          sock.send({ type: "dm_init", payload: { target_agent: targetRef.current } });
          break;
        }
        case "dm_init": {
          // 握手响应：替换 messages 为 history
          // Bug 1 升级: history 类型是 AgentMemoryEntry[] (新) 或 DmMessageWire[] (旧)
          const p = ev.payload;
          const hist = p.history ?? [];
          setInit({
            phase: "ready",
            targetAgent: p.target_agent,
            name: p.name,
            emoji: p.emoji,
            memorySize: p.memory_size ?? hist.length,
          });
          setMessages(hist.map((e, i) => historyEntryToMsg(e, i)));
          break;
        }
        case "dm_msg_ack": {
          // 把最近的 user 消息标记为 ack（按时间戳找最近一条未 ack 的）
          setMessages((prev) => {
            const idx = [...prev].reverse().findIndex(
              (m) => m.kind === "user" && m.status === "sent"
            );
            if (idx < 0) return prev;
            const realIdx = prev.length - 1 - idx;
            const next = prev.slice();
            const u = next[realIdx];
            if (u.kind === "user") {
              next[realIdx] = { ...u, status: "ack" };
            }
            return next;
          });
          break;
        }
        case "dm_thinking": {
          // Bug 1: 一次性绑定身份 —— 此 bubble 后续 chunk 不会再调 resolveRole
          const bound = bindIdentity(ev.payload.agent);
          const id = newId();
          streamingIdRef.current = id;
          setMessages((prev) => [
            ...prev,
            {
              id,
              kind: "agent",
              text: "",
              timestamp: ts,
              isStreaming: true,
              agentKey: bound.agentKey,
              agentName: ev.payload.name ?? bound.agentName,
              agentEmoji: ev.payload.emoji ?? bound.agentEmoji,
              source: "dm", // 实时流默认是 dm
            },
          ]);
          break;
        }
        case "dm_msg_chunk": {
          const chunk = ev.payload.chunk ?? "";
          const existingId = streamingIdRef.current;
          if (existingId) {
            // Bug 1 关键变更: 正常路径只追加 chunk + 维持 streaming 状态
            // 不再 resolveRole / 覆写 agentKey / 覆写 agentName
            setMessages((prev) =>
              prev.map((m) =>
                m.id === existingId && m.kind === "agent"
                  ? { ...m, text: m.text + chunk, isStreaming: true }
                  : m
              )
            );
          } else {
            // Bug 1 RACE: chunk 比 thinking 先到 / thinking 丢失
            // 兜底：自己造一个 bubble (不再静默丢 chunk)
            const bound = bindIdentity(ev.payload.agent);
            const id = newId();
            streamingIdRef.current = id;
            setMessages((prev) => [
              ...prev,
              {
                id,
                kind: "agent",
                text: chunk,
                timestamp: ts,
                isStreaming: true,
                agentKey: bound.agentKey,
                agentName: bound.agentName,
                agentEmoji: bound.agentEmoji,
                source: "dm",
              },
            ]);
          }
          break;
        }
        case "dm_done": {
          const id = streamingIdRef.current;
          streamingIdRef.current = null;
          const full = ev.payload.full_text ?? "";
          // Bug 1: dm_done 只补全 name/emoji (若 server 提供) — agentKey 沿用绑定值
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== id || m.kind !== "agent") return m;
              return {
                ...m,
                text: full || m.text,
                isStreaming: false,
                agentName: ev.payload.name ?? m.agentName,
                agentEmoji: ev.payload.emoji ?? m.agentEmoji,
                // agentKey 不变 (沿用 thinking 时绑定)
              };
            })
          );
          // user 的 +1 已在 send() 那里做了；这里 +1 = agent 持久化那一行
          setInit((cur) =>
            cur.phase === "ready" ? { ...cur, memorySize: cur.memorySize + 1 } : cur
          );
          break;
        }
        case "dm_error": {
          const code = ev.payload.code ?? "DM_ERROR";
          const msg = ev.payload.message ?? "DM 出现异常";
          setMessages((prev) => [
            ...prev,
            {
              id: `dm-err-${ts}`,
              kind: "system",
              text: `${code}: ${msg}`,
              timestamp: ts,
              status: code,
            },
          ]);
          // 关键错误: 标记 init 失败（如 INIT_FAILED / EMPTY_TARGET / UNKNOWN_AGENT）
          if (
            code === "UNKNOWN_AGENT" ||
            code === "EMPTY_TARGET" ||
            code === "NOT_IN_DM_MODE"
          ) {
            setInit({ phase: "failed", code, message: msg });
          }
          break;
        }
        // 群聊事件在 DM 模式下不应该出现（路由层阻塞），但若出现则忽略
        case "user_msg_ack":
        case "supervisor_decision":
        case "agent_thinking":
        case "agent_msg_chunk":
        case "agent_done":
        case "max_rounds_reached":
        case "group_chat_done":
          break;
        case "error": {
          // 通用 error（如 BAD_JSON / UNKNOWN_TYPE）也并入消息流
          setMessages((prev) => [
            ...prev,
            {
              id: `err-${ts}`,
              kind: "system",
              text: ev.payload.message ?? "Unknown error",
              timestamp: ts,
              status: `Error: ${ev.payload.code ?? "?"}`,
            },
          ]);
          break;
        }
        case "pong":
          break;
      }
    });

    sock.connect();

    return () => {
      offStatus();
      offEvent();
      sock.destroy();
      socketRef.current = null;
    };
    // 当 target 或 sessionId 变化时重建；同时内部读 targetRef
  }, [target, sessionId]);

  const send = useCallback(
    (text: string): boolean => {
      const trimmed = text.trim();
      if (!trimmed) return false;
      if (init.phase !== "ready") return false;
      const sock = socketRef.current;
      if (!sock) return false;
      // 乐观 echo：先把 user 消息塞进列表，再发 ws（server ack 时 status=sent→ack）
      const msgId = newId();
      // T9 / Piece B: echo 时附 author, 让 user bubble 立即显示署名
      // （服务端 ack 后 status=sent→ack, 不重新设 author, 保持一致）
      const echoAuthor = getDisplayName();
      const echo: DmChatMessage = {
        id: msgId,
        kind: "user",
        text: trimmed,
        timestamp: Date.now(),
        status: "sent",
        author: echoAuthor,
      };
      setMessages((prev) => [...prev, echo]);
      // 也把 init.memorySize +1（user）即时反映在 header
      setInit((cur) => (cur.phase === "ready" ? { ...cur, memorySize: cur.memorySize + 1 } : cur));
      // T9 / Piece B: 带上 userIdentity 署名 (默认 "神秘人")。
      // 后端 dm_msg handler 读 payload.author 持久化到 AgentMemoryEntry.author。
      const dmAuthor = getDisplayName();
      const ok = sock.send({
        type: "dm_msg",
        payload: { text: trimmed, msg_id: msgId, author: dmAuthor },
      });
      if (!ok) {
        // 失败时把那条 user 消息标记为 fail
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msgId && m.kind === "user" ? { ...m, status: "fail" } : m
          )
        );
        return false;
      }
      return true;
    },
    [init]
  );

  const interrupt = useCallback(() => {
    const sock = socketRef.current;
    if (!sock) return;
    sock.send({ type: "dm_interrupt", payload: {} });
  }, []);

  const reconnect = useCallback(
    (opts?: { forceNewSession?: boolean }) => {
      // T9: default = rebuild socket only, keep persisted sid (WeChat-like).
      // Explicit `forceNewSession: true` mints a fresh sid AND persists it,
      // which the Clear button uses to wipe the visible conversation.
      if (opts?.forceNewSession) {
        setSessionId(`dm-${targetRef.current}-${newId()}`);
      } else {
        // Bump the sessionId to the same persisted value via setState to force
        // the effect (which rebuilds the socket) to re-run. We round-trip
        // through setSessionId so React still tears down + rebuilds the WS.
        setSessionId((cur) => cur);
      }
    },
    []
  );

  /** T9 / Piece C: clear the local message list and force a brand-new sid.
   *  Frontend-only operation — does NOT delete anything from backend SQLite;
   *  the Clear button issues a separate DELETE /api/dm/history call for that.
   *  Used after the backend confirms row deletion so the UI shows an empty
   *  conversation immediately.
   */
  const clearLocalHistory = useCallback(() => {
    setMessages([]);
    setInit({ phase: "loading" });
    streamingIdRef.current = null;
    setSessionId(`dm-${targetRef.current}-${newId()}`);
  }, []);

  return useMemo<DmContextValue>(
    () => ({ status, init, messages, send, interrupt, reconnect, sessionId, clearLocalHistory }),
    [status, init, messages, send, interrupt, reconnect, sessionId, clearLocalHistory]
  );
}
