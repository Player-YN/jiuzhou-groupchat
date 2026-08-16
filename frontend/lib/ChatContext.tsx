"use client";

/** ChatContext — shared state for ChatRoom.
 *  Wraps ChatSocket (lib/ws.ts) and provides:
 *   - status: ConnStatus
 *   - messages: ChatMessage[]
 *   - send(text): push a user_msg + start listening
 *   - reconnect(): manual reconnect
 *
 *  角色化：所有 AI 消息的 speakerName / speakerEmoji / agentKey 来自 server event，
 *  前端不再硬编码 "Host"。见 lib/ws.ts 的 ROLE_META。
 *
 *  Bug 1 修复 (2026-07-02)：身份绑定语义
 *  - 每个 streaming bubble 的身份在 agent_thinking 事件**一次性绑定**
 *  - 后续 agent_msg_chunk 只追加文本 + 设置 isStreaming，不再修改 speakerName/speakerEmoji/agentKey
 *  - agent_done 只补 name/emoji (若 server 提供) — 不再修改 agentKey
 *  - RACE: 若 chunk 比 thinking 先到, 用 chunk.agent 合成 bubble 并绑定身份
 *  - 之前版本会在每个 chunk 用 resolveRole 重解析, 导致:
 *      * server 一开始传错 agent → 后续 chunk 永远错位
 *      * server 偶尔不传 agent → 身份被重置成 "AI" / "✨"
 *      * name emoji 与 agentKey 解耦, 颜色对不上名字
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ChatSocket,
  defaultApiBase,
  defaultSocketURL,
  resolveRole,
  ROLE_META,
  type ConnStatus,
  type RoleKey,
  type ServerEvent,
} from "@/lib/ws";

import { getDisplayName, useUserIdentity } from "@/lib/userIdentity";

/** AI 消息的展示元数据。speakerName / speakerEmoji 来自 server。 */
export type ChatMessage =
  | {
      id: string;
      role: "user";
      text: string;
      timestamp: number;
      status: "sent";
      /**
       * T9 / Piece B: 人类用户的署名 (默认 "神秘人" / 来自 userIdentity)。
       * 渲染时用于显示 "我: 凡人" 而不是无名。
       * 后端会把它持久化到 AgentMemoryEntry.author。
       */
      author?: string;
    }
  | {
      id: string;
      role: "ai";
      text: string;
      timestamp: number;
      isStreaming: boolean;
      speakerName: string; // 服务端 name 字段（如 "小主"）
      speakerEmoji: string; // 服务端 emoji 字段（如 "🎙️"）
      agentKey: RoleKey | null; // 解析后的角色 key（用于颜色/头像）
      round?: number;
    }
  | { id: string; role: "system"; text: string; timestamp: number; status?: string };

type ChatContextValue = {
  status: ConnStatus;
  messages: ChatMessage[];
  /**
   * T9 / Piece C: 直接 setMessages 给 ChatRoom Clear 按钮用 —
   * DELETE 后端 + 清本地数组 + 立即刷新 UI (不必等下次 socket 推送).
   * 一般组件应只通过 send() 间接改 messages.
   */
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  send: (text: string) => void;
  /**
   * T9 / Piece C: 支持 { forceNewSession: true } 选项 — Clear 按钮用它强制
   * 一个新 sid (同时 reconnect socket). 默认是仅重建 socket, 复用 sid.
   */
  reconnect: (opts?: { forceNewSession?: boolean }) => void;
  agents: string[];
  topic: string | null;
  sessionId: string;
  sessionInitAt: number | null;
  /** 当前会话参与的角色 key 集合（顺序固定：host → creator → critic → summarizer） */
  agentKeys: RoleKey[];
  /**
   * T9 / Piece B: 当前用户的 display name + setter. Header bar reads this to
   * render "我: 神秘人 ✎" with an inline editor; send() reads it to fill the
   * `author` field of the user_msg payload so backend can persist署名.
   */
  displayName: string;
  setDisplayName: (name: string) => void;
};

const ChatContext = createContext<ChatContextValue | null>(null);

function newId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Bug 1: 一次性身份绑定 —— 拿到 agent 字符串 (RoleKey | 角色名) 后, 用 ROLE_META
 *  推导一致的 (agentKey, speakerName, speakerEmoji)。若 resolveRole 失败, 返回 null
 *  agentKey (后续渲染层 fallback 到中性 AI 主题, 不假装是某角色)。
 *  这就是"绑定" —— 同一个 streaming bubble 内, 后续 chunk 沿用此结果, 不会再调用 resolveRole。
 */
function bindIdentity(agent: string | undefined): {
  agentKey: RoleKey | null;
  speakerName: string;
  speakerEmoji: string;
} {
  const k = resolveRole(agent);
  if (k && ROLE_META[k]) {
    return { agentKey: k, speakerName: ROLE_META[k].name, speakerEmoji: ROLE_META[k].emoji };
  }
  // 不可识别 —— 不假装是某角色, 不显示 "Host" 或 "AI"
  // 用空 string + ✨ 触发 ChatBubble 的中性主题 fallback (避免 name=undefined 渲染报错)
  return { agentKey: null, speakerName: "", speakerEmoji: "\u2728" };
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [topic, setTopic] = useState<string | null>(null);
  const [sessionInitAt, setSessionInitAt] = useState<number | null>(null);
  const [agentKeys, setAgentKeys] = useState<RoleKey[]>([]);

  // T9 / Piece B: 用户身份 — 默认 "神秘人"，可在 header 点击 ✎ 改。
  // hooks 必须无条件 top-level 调用, 不能放在条件分支里。
  const { displayName, setDisplayName } = useUserIdentity();

  const socketRef = useRef<ChatSocket | null>(null);
  // Track the currently streaming AI message id (so subsequent chunks append to it)
  const streamingAiIdRef = useRef<string | null>(null);

  // Stable session id (regenerated on reconnect to bust any server-side state).
  // T6: persisted to localStorage so reload reuses the same id and the
  // server returns the persisted group history on /api/group/history.
  const [sessionId, setSessionId] = useState<string>(() => {
    if (typeof window !== "undefined") {
      try {
        const stored = window.localStorage.getItem("chat-session-id");
        if (stored && typeof stored === "string" && stored.length > 0) {
          return stored;
        }
      } catch {
        // localStorage may be blocked (privacy mode); fall through
      }
    }
    return `s-${newId()}`;
  });

  // T6: mirror sessionId → localStorage whenever it changes
  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem("chat-session-id", sessionId);
      } catch {
        // ignore quota / privacy errors; not fatal
      }
    }
  }, [sessionId]);

  // T6: on session change, fetch persisted group history from
  // GET /api/group/history and prepend to local messages list.
  // Runs once per sessionId; aborts in-flight if sessionId changes.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const ac = new AbortController();
    const limit = 100;
    const baseHttp = defaultApiBase();
    fetch(
      `${baseHttp}/api/group/history?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`,
      { signal: ac.signal },
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!body || !Array.isArray(body.history)) return;
        const historical: ChatMessage[] = body.history.map((ev: any) => {
          if (ev.role === "user") {
            return {
              id: `hist-${ev.timestamp ?? Math.random()}-${Math.random().toString(36).slice(2, 6)}`,
              role: "user" as const,
              text: ev.text ?? "",
              timestamp: ev.timestamp ?? Date.now(),
              isStreaming: false,
              status: "sent" as const,
              // T9 / Piece B: 从 backend 拉回的 author (default "神秘人" if null)
              author: ev.author ?? "神秘人",
            };
          }
          // Fix: history must bind agentKey from speaker_key / agent_name
          // so avatar click → RoleProfile works for reloaded messages too.
          const bound = bindIdentity(
            ev.speaker_key ?? ev.agent_name ?? undefined
          );
          return {
            id: `hist-${ev.timestamp ?? Math.random()}-${Math.random().toString(36).slice(2, 6)}`,
            role: "ai" as const,
            text: ev.text ?? "",
            timestamp: ev.timestamp ?? Date.now(),
            isStreaming: false,
            speakerName: (ev.agent_name as string) || bound.speakerName,
            speakerEmoji: (ev.agent_emoji as string) || bound.speakerEmoji,
            agentKey: bound.agentKey,
          };
        });
        if (historical.length === 0) return;
        // Prepend, dedupe by id (defensive), sort ASC
        setMessages((prev) => {
          const existingIds = new Set(prev.map((m) => m.id));
          const fresh = historical.filter((m) => !existingIds.has(m.id));
          if (fresh.length === 0) return prev;
          const merged = [...prev, ...fresh].sort(
            (a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0),
          );
          return merged;
        });
      })
      .catch((err) => {
        // AbortError on session change is expected; ignore others silently
        // (history is a nice-to-have; live WS still works).
        if (err?.name !== "AbortError") {
          // eslint-disable-next-line no-console
          console.warn("[chat] history load failed:", err);
        }
      });
    return () => ac.abort();
  }, [sessionId]);

  // Build socket once
  useEffect(() => {
    const url = defaultSocketURL(sessionId);
    const sock = new ChatSocket(url);
    socketRef.current = sock;

    const offStatus = sock.onStatus((s) => setStatus(s));

    const offEvent = sock.onEvent((ev: ServerEvent) => {
      const ts = ev.ts;
      switch (ev.type) {
        case "session_init": {
          // ConnectionStatus in header already covers ready state — do not
          // enqueue a long welcome system pill into the message list.
          setSessionInitAt(ts);
          setAgents(ev.payload.agents ?? []);
          setTopic(ev.payload.topic ?? null);
          // 解析 agent keys（Stage 5-B：6 九洲一号群角色顺序）
          const keys: RoleKey[] = [
            "shu-hang",
            "yao-shi",
            "san-lang",
            "bei-he",
            "bai-qianbei",
            "ling-die",
          ];
          setAgentKeys(keys);
          break;
        }
        case "user_msg_ack": {
          // WeChat-style hygiene: no "✓ sent" protocol pill in the chat stream.
          // Optimistic local echo already shows the user bubble.
          break;
        }
        case "supervisor_decision": {
          // Internal compatibility event. Scheduling machinery should not be
          // visible inside a fictional social group.
          break;
        }
        case "agent_thinking": {
          // Bug 1: 一次性绑定身份 —— 此 bubble 后续 chunk 不会再调 resolveRole
          const bound = bindIdentity(ev.payload.agent);
          const aiId = newId();
          streamingAiIdRef.current = aiId;
          setMessages((prev) => [
            ...prev,
            {
              id: aiId,
              role: "ai",
              text: "",
              timestamp: ts,
              isStreaming: true,
              // server event 的 name/emoji 若提供, 优先 (覆盖 ROLE_META 默认)
              // — 但 agentKey 已绑定, 不再变化
              speakerName: ev.payload.name ?? bound.speakerName,
              speakerEmoji: ev.payload.emoji ?? bound.speakerEmoji,
              agentKey: bound.agentKey,
              round: ev.payload.round,
            },
          ]);
          break;
        }
        case "agent_msg_chunk": {
          const chunk = ev.payload.chunk ?? "";
          const aiId = streamingAiIdRef.current;
          if (!aiId) {
            // Bug 1 RACE: chunk 比 thinking 先到 (或 thinking 丢失) — 兜底合成 bubble
            // 用 chunk.agent 绑定身份 (与 dm_init 沿用同一 speaker_key 同义)
            const newAiId = newId();
            streamingAiIdRef.current = newAiId;
            const bound = bindIdentity(ev.payload.agent);
            setMessages((prev) => [
              ...prev,
              {
                id: newAiId,
                role: "ai",
                text: chunk,
                timestamp: ts,
                isStreaming: true,
                speakerName: bound.speakerName,
                speakerEmoji: bound.speakerEmoji,
                agentKey: bound.agentKey,
                round: ev.payload.round,
              },
            ]);
            break;
          }
          // Bug 1 关键变更: 正常路径只追加 chunk + 维持 streaming 状态
          // 不再调用 resolveRole / 覆写 agentKey / 覆写 speakerName
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aiId && m.role === "ai"
                ? {
                    ...m,
                    text: m.text + chunk,
                    isStreaming: true,
                    round: m.round ?? ev.payload.round,
                  }
                : m
            )
          );
          break;
        }
        case "agent_done": {
          const aiId = streamingAiIdRef.current;
          streamingAiIdRef.current = null;
          const finalText = ev.payload.full_text ?? "";
          // Bug 1: agent_done 只补全 name/emoji (若 server 提供) — agentKey 沿用绑定值
          // 这是为了应对 server 偶尔补发更准确的中文名/emoji, 但不破坏已经绑定的角色
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== aiId || m.role !== "ai") return m;
              return {
                ...m,
                text: finalText || m.text,
                isStreaming: false,
                speakerName: ev.payload.name ?? m.speakerName,
                speakerEmoji: ev.payload.emoji ?? m.speakerEmoji,
                // agentKey 不变 (沿用 thinking 时绑定)
                round: ev.payload.round ?? m.round,
              };
            })
          );
          break;
        }
        case "max_rounds_reached": {
          setMessages((prev) => [
            ...prev,
            {
              id: `max-${ts}`,
              role: "system",
              text: `Reached max rounds (${ev.payload.max_rounds})`,
              timestamp: ts,
              status: "max_rounds",
            },
          ]);
          break;
        }
        case "group_chat_done": {
          // Turn boundary is internal; silence is a valid social outcome.
          break;
        }
        case "cron_agent_post": {
          const bound = bindIdentity(ev.payload.role_key);
          const messageId = ev.payload.event_id
            ? `proactive-${ev.payload.event_id}`
            : `proactive-${ts}-${ev.payload.role_key}`;
          setMessages((prev) => {
            // Reconnect/fan-out retries may deliver the same event again.
            if (prev.some((message) => message.id === messageId)) return prev;
            return [
              ...prev,
              {
                id: messageId,
                role: "ai",
                text: ev.payload.full_text,
                timestamp: ts,
                isStreaming: false,
                speakerName: ev.payload.name ?? bound.speakerName,
                speakerEmoji: ev.payload.emoji ?? bound.speakerEmoji,
                agentKey: bound.agentKey,
              },
            ];
          });
          break;
        }
        case "error": {
          setMessages((prev) => [
            ...prev,
            {
              id: `err-${ts}`,
              role: "system",
              text: ev.payload.message ?? "Unknown error",
              timestamp: ts,
              status: `Error: ${ev.payload.code ?? "?"}`,
            },
          ]);
          break;
        }
        case "pong":
          // heartbeat — silent
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
  }, [sessionId]);

  // Auto-scroll the message list as new messages arrive.
  useEffect(() => {
    // Defer to allow DOM to update first
    const t = setTimeout(() => {
      const el = document.getElementById("chat-bottom-anchor");
      el?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, 30);
    return () => clearTimeout(t);
  }, [messages.length]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const msgId = newId();
      // 1) Local echo bubble (optimistic)
      const author = getDisplayName();
      setMessages((prev) => [
        ...prev,
        {
          id: msgId,
          role: "user",
          text: trimmed,
          timestamp: Date.now(),
          status: "sent",
          author,
        },
      ]);
      // 2) Send via socket
      const sock = socketRef.current;
      if (!sock) return;
      // T9 / Piece B: 把 userIdentity 的署名带过去 (后端 fallback "神秘人")
      // — author 已在上面 setMessages 块前同步获取, 保持一致。
      const ok = sock.send({
        type: "user_msg",
        session_id: sessionId,
        payload: { text: trimmed, msg_id: msgId, author },
      });
      if (!ok) {
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: "system",
            text: "WebSocket not open",
            timestamp: Date.now(),
            status: "Error: WS_CLOSED",
          },
        ]);
      }
    },
    [sessionId]
  );

  const reconnect = useCallback(
    (opts?: { forceNewSession?: boolean }) => {
      // T9 / Piece C: 默认 = 仅重建 socket, 复用持久化 sid (WeChat-like).
      // forceNewSession: true = 清后端后用全新 sid (Clear 按钮路径).
      if (opts?.forceNewSession) {
        // 不塞 "Reconnecting..." 系统消息 — Clear 按钮已清空 messages
        setSessionId(`s-${newId()}`);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: `sys-${Date.now()}`,
            role: "system",
            text: "Reconnecting\u2026",
            timestamp: Date.now(),
            status: "Reconnecting",
          },
        ]);
        setSessionId(`s-${newId()}`);
      }
      // The sessionId change triggers the effect above to rebuild the socket.
    },
    []
  );

  const value = useMemo<ChatContextValue>(
    () => ({
      status,
      messages,
      // T9 / Piece C: 直接暴露给 ChatRoom Clear 按钮用
      setMessages,
      send,
      reconnect,
      agents,
      topic,
      sessionId,
      sessionInitAt,
      agentKeys,
      // T9 / Piece B: 用户身份
      displayName,
      setDisplayName,
    }),
    [
      status,
      messages,
      setMessages,
      send,
      reconnect,
      agents,
      topic,
      sessionId,
      sessionInitAt,
      agentKeys,
      displayName,
      setDisplayName,
    ]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
