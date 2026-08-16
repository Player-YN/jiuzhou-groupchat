// ===== WebSocket 客户端：原生 WebSocket + 自动重连（指数退避 1s/2s/4s/8s/16s/30s 上限）
// 单例状态机，所有 React 组件通过 ChatContext 共享。
"use client";

export type ConnStatus = "connecting" | "connected" | "disconnected" | "reconnecting" | "error";

/** 九洲一号群聊天群 6 角色 key（与 backend/app/graph.py ROLES 字典保持一致） */
export type RoleKey =
  | "shu-hang"
  | "yao-shi"
  | "san-lang"
  | "bei-he"
  | "bai-qianbei"
  | "ling-die";

/** 角色元数据（前端用，定义颜色 / emoji / 显示名 / 境界） */
export type RoleMeta = {
  key: RoleKey;
  /** 中文显示名（"宋书航"） */
  name: string;
  /** 大 emoji 头像 */
  emoji: string;
  /** 境界（"灵尊" / "八品药师" / "六品刀修" / "八品散修" / "九品之上" / "八品尊者"） */
  realm: string;
  /** 简短境界（"灵尊" / "八品药" / "六品刀" / "八品散" / "九品" / "八品尊"） */
  realmShort: string;
  /** Tailwind / 内联 CSS 主题色 */
  gradient: string; // 渐变背景 (from -> to)
  ring: string; // 边框/光环
  text: string; // 名字颜色
  /** 气泡左边 4px 装饰条 + 头像边框 + sidebar 卡片左边 */
  accent: string; // 例 "via-amber-500" / "from-rose-500"
  accentHex: string; // 16 进制色值（用于 @mention 高亮 hover 等场景）
  /** 状态点颜色 */
  dot: string;
  /** LLM provider（minimax / agnes） */
  provider: "minimax" | "agnes";
  /** 一句话描述 */
  blurb: string;
};

/** 6 九洲一号群角色统一元数据（前后端共享 Key + 境界）
 *
 *  配色策略：每个角色一个独特色相，区分"六道众生"：
 *    - shu-hang     琥珀金  (主角, 灵尊)
 *    - yao-shi      翡翠绿  (丹道, 八品药师)
 *    - san-lang     朱砂红  (刀修, 六品刀修)
 *    - bei-he       天青蓝  (水系, 八品散修)
 *    - bai-qianbei  霜白紫  (神秘, 九品之上)
 *    - ling-die     蝶粉紫  (蝴蝶, 八品尊者)
 */
export const ROLE_META: Record<RoleKey, RoleMeta> = {
  "shu-hang": {
    key: "shu-hang",
    name: "宋书航",
    emoji: "🌟",
    realm: "灵尊",
    realmShort: "灵尊",
    gradient: "from-amber-400 via-yellow-500 to-amber-600",
    ring: "ring-[#C7A969]/50",
    text: "text-[#D4B574]",
    accent: "from-[#C7A969] to-[#D4B574]",
    accentHex: "#C7A969",
    dot: "bg-[#C7A969]",
    provider: "minimax",
    blurb: "九洲一号群主角 · 灵尊",
  },
  "yao-shi": {
    key: "yao-shi",
    name: "药师",
    emoji: "💊",
    realm: "八品药师",
    realmShort: "八品药",
    gradient: "from-emerald-500 via-teal-500 to-emerald-600",
    ring: "ring-[#5C7367]/60",
    text: "text-[#7A9387]",
    accent: "from-[#5C7367] to-[#7A9387]",
    accentHex: "#5C7367",
    dot: "bg-[#5C7367]",
    provider: "minimax",
    blurb: "丹道宗师 · 八品药师",
  },
  "san-lang": {
    key: "san-lang",
    name: "狂刀三浪",
    emoji: "🗡️",
    realm: "六品刀修",
    realmShort: "六品刀",
    gradient: "from-rose-500 via-red-600 to-rose-700",
    ring: "ring-[#8B3A3A]/60",
    text: "text-[#A84545]",
    accent: "from-[#8B3A3A] to-[#A84545]",
    accentHex: "#8B3A3A",
    dot: "bg-[#8B3A3A]",
    provider: "minimax",
    blurb: "刀修狂人 · 六品刀修",
  },
  "bei-he": {
    key: "bei-he",
    name: "北河散人",
    emoji: "🌊",
    realm: "八品散修",
    realmShort: "八品散",
    gradient: "from-sky-500 via-blue-600 to-sky-700",
    ring: "ring-[#6A8AAD]/60",
    text: "text-[#8FB0CE]",
    accent: "from-[#6A8AAD] to-[#8FB0CE]",
    accentHex: "#6A8AAD",
    dot: "bg-[#6A8AAD]",
    provider: "agnes",
    blurb: "元老前辈 · 八品散修",
  },
  "bai-qianbei": {
    key: "bai-qianbei",
    name: "白前辈",
    emoji: "👻",
    realm: "九品之上",
    realmShort: "九品上",
    gradient: "from-slate-300 via-zinc-300 to-slate-500",
    ring: "ring-[#B8B0A2]/50",
    text: "text-[#D4CCBC]",
    accent: "from-[#B8B0A2] to-[#D4CCBC]",
    accentHex: "#B8B0A2",
    dot: "bg-[#B8B0A2]",
    provider: "agnes",
    blurb: "辈分最高 · 九品之上",
  },
  "ling-die": {
    key: "ling-die",
    name: "灵蝶尊者",
    emoji: "🦋",
    realm: "八品尊者",
    realmShort: "八品尊",
    gradient: "from-fuchsia-400 via-purple-500 to-fuchsia-600",
    ring: "ring-[#B07AB0]/60",
    text: "text-[#C99BC9]",
    accent: "from-[#B07AB0] to-[#C99BC9]",
    accentHex: "#B07AB0",
    dot: "bg-[#B07AB0]",
    provider: "minimax",
    blurb: "灵蝶岛主 · 八品尊者",
  },
};

/** 角色出现顺序（与 backend ROLE_CYCLE 一致） */
export const ROLE_CYCLE: RoleKey[] = [
  "shu-hang",
  "yao-shi",
  "san-lang",
  "bei-he",
  "bai-qianbei",
  "ling-die",
];

/** 角色反查（用 server 返回的 agent 字符串找 RoleKey）
 *  支持中英文 + 短名匹配：
 *    - agent 字段（"shu-hang"）→ 直接命中
 *    - 中文名（"宋书航"）→ ROLE_META[k].name 命中
 *    - 中文短名（"三浪" → 狂刀三浪）→ 前缀/后缀匹配
 */
export function resolveRole(agent: string | undefined | null): RoleKey | null {
  if (!agent) return null;
  const a = agent.toLowerCase().trim();
  // 1) 直接 key 命中
  if (a in ROLE_META) return a as RoleKey;
  // 2) 中文名精确匹配
  for (const k of ROLE_CYCLE) {
    if (ROLE_META[k].name === agent) return k;
  }
  // 3) 容错匹配——仅针对中文短名，避免与 agent key 混淆
  //    关键：单字 fallback 太容易误匹配（"白"/"药"/"蝶" 会匹配任意含该字的字符串）
  //    改用至少 2 字的中文昵称/简称，且必须精确匹配
  if (agent.includes("书航")) return "shu-hang";
  if (agent.includes("三浪")) return "san-lang";
  if (agent.includes("北河")) return "bei-he";
  if (agent.includes("白前辈")) return "bai-qianbei";  // 必须全称，避免误匹配其他含"白"的字段
  if (agent.includes("灵蝶")) return "ling-die";      // 全称，避免"蝴蝶"等含"蝶"的无关词
  return null;
}

/**
 * 协议事件 payload 形状（与 backend/app/routers/ws.py + models.py 对齐）
 * 所有 server 发出的 event 都有 type / session_id / payload / ts 四个字段。
 */

/** 持久化的 DM 历史条目 — 旧 schema (向后兼容用)
 *  保留: 旧 dm_history 表的 DmMessage 字段,前端 fallback 解析时仍可用
 */
export type DmMessageWire = {
  role: "user" | "agent";
  text: string;
  timestamp: number;
  agent_key?: string | null;
  agent_name?: string | null;
  agent_emoji?: string | null;
  // 新 schema (AgentMemoryEntry) 字段 — 可选,backend 没升级时不存在
  source?: "group" | "dm";
  speaker_key?: string | null;
};

/** Per-agent unified memory entry (Bug 2 backend 升级后使用)
 *  与 backend AgentMemoryEntry Pydantic schema 对齐
 *  - role:       发言角色 (user / agent)
 *  - source:     来源场景 — group (群聊 fan-out) / dm (私聊)
 *  - speaker_key: 实际发言者 — "user" 或 6 九洲一号群角色 key 之一
 *                (与 agent_key 不同: agent_key 是 memory owner, speaker_key 是说话的人)
 *  - agent_key:  memory owner (dm 模式 = 私信对象, group fan-out = 6 角色之一)
 *  - agent_name/emoji: 冗余,加速前端渲染 (speaker_key 是 agent 时填)
 */
export type AgentMemoryEntry = {
  role: "user" | "agent";
  source: "group" | "dm";
  speaker_key: string;
  text: string;
  timestamp: number;
  agent_key?: string | null;
  agent_name?: string | null;
  agent_emoji?: string | null;
};

/** 持久化的 DM 历史条目 — 新 schema (Bug 2 升级后)
 *  现在 backend dm_init 响应的 history 字段是 AgentMemoryEntry[]
 */
export type DmHistoryEntry = AgentMemoryEntry;

export type ServerEvent =
  | {
      type: "session_init";
      session_id: string;
      payload: {
        agents?: string[]; // ["宋书航 🌟", "药师 💊", ...]
        topic?: string | null;
        agents_in_session?: { key: RoleKey; name: string; emoji: string }[];
        max_rounds?: number;
      };
      ts: number;
    }
  | {
      type: "user_msg_ack";
      session_id: string;
      payload: { text?: string; status?: string; event_id?: string };
      ts: number;
    }
  | {
      type: "supervisor_decision";
      session_id: string;
      payload: { next_agent?: string; reasoning?: string };
      ts: number;
    }
  | {
      type: "agent_thinking";
      session_id: string;
      payload: { agent: RoleKey | string; name?: string; emoji?: string; round?: number };
      ts: number;
    }
  | {
      type: "agent_msg_chunk";
      session_id: string;
      payload: { agent: RoleKey | string; chunk: string; round?: number };
      ts: number;
    }
  | {
      type: "agent_done";
      session_id: string;
      payload: {
        agent: RoleKey | string;
        name?: string;
        emoji?: string;
        full_text?: string;
        round?: number;
      };
      ts: number;
    }
  | {
      type: "max_rounds_reached";
      session_id: string;
      payload: { max_rounds: number };
      ts: number;
    }
  | {
      type: "group_chat_done";
      session_id: string;
      payload: { rounds: number; agents: (RoleKey | string)[] };
      ts: number;
    }
  | {
      /** Event-driven proactive NPC post (legacy cron and MVP coordinator). */
      type: "cron_agent_post";
      session_id: string;
      payload: {
        role_key: RoleKey | string;
        name?: string;
        emoji?: string;
        full_text: string;
        source?: "npc_loop" | "behavior_coordinator" | string;
        event_id?: string;
      };
      ts: number;
    }
  | {
      // ===== Stage 6 DM Phase 2 + Bug 2 升级 =====
      // 服务端推回 client 发起的 dm_init 握手结果
      type: "dm_init";
      session_id: string;
      payload: {
        target_agent: RoleKey | string;
        name: string;
        emoji: string;
        /** Bug 2 升级: history 类型从 DmMessageWire[] 改为 AgentMemoryEntry[]
         *  新增 source (group/dm) + speaker_key (实际发言者, "user" 或 6 角色 key)
         *  旧 backend 兼容: 后端没升级时仍可发送旧 DmMessage shape,前端 fallback 解析
         */
        history: AgentMemoryEntry[] | DmMessageWire[];
        memory_size: number;
      };
      ts: number;
    }
  | {
      // 服务端确认收到 dm_msg
      type: "dm_msg_ack";
      session_id: string;
      payload: {
        target_agent?: RoleKey | string;
        text?: string;
        status?: string;
        event_id?: string;
      };
      ts: number;
    }
  | {
      // 目标 AI 开始思考
      type: "dm_thinking";
      session_id: string;
      payload: { agent?: RoleKey | string; name?: string; emoji?: string };
      ts: number;
    }
  | {
      // 流式 token（增量）
      type: "dm_msg_chunk";
      session_id: string;
      payload: { agent?: RoleKey | string; chunk: string };
      ts: number;
    }
  | {
      // 目标 AI 完成回复
      type: "dm_done";
      session_id: string;
      payload: {
        agent?: RoleKey | string;
        name?: string;
        emoji?: string;
        full_text?: string;
      };
      ts: number;
    }
  | {
      // DM 路径异常（target 非法、未 dm_init、stream 异常、持久化失败等）
      type: "dm_error";
      session_id: string;
      payload: { code?: string; message?: string };
      ts: number;
    }
  | {
      type: "error";
      session_id: string;
      payload: { code?: string; message?: string };
      ts: number;
    }
  | {
      type: "pong";
      session_id: string;
      payload: Record<string, unknown>;
      ts: number;
    };

export type ClientEvent =
  | {
      type: "user_msg";
      session_id: string;
      payload: {
        text: string;
        msg_id?: string;
        /**
         * T9 / Piece B: 人类用户署名 (默认 "神秘人")。后端会持久化到
         * AgentMemoryEntry.author, 渲染 user bubble 时显示 "凡人: ..."。
         * 缺省 / null / 空字符串 时后端 fallback "神秘人"。
         */
        author?: string;
      };
    }
  | { type: "ping"; session_id?: string; payload?: Record<string, unknown> }
  | { type: "interrupt"; session_id?: string; payload?: Record<string, unknown> }
  // ===== Stage 6 DM Phase 2 =====
  | { type: "dm_init"; session_id?: string; payload: { target_agent: string } }
  | {
      type: "dm_msg";
      session_id?: string;
      payload: {
        text: string;
        msg_id?: string;
        /** T9 / Piece B: 同 user_msg.author, DM 也带署名。 */
        author?: string;
      };
    }
  | { type: "dm_interrupt"; session_id?: string; payload?: Record<string, unknown> };

type Listener = (ev: ServerEvent) => void;
type StatusListener = (s: ConnStatus) => void;

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

export class ChatSocket {
  private url: string;
  private ws: WebSocket | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private destroyed = false;
  private status: ConnStatus = "disconnected";

  private listeners: Set<Listener> = new Set();
  private statusListeners: Set<StatusListener> = new Set();

  constructor(url: string) {
    this.url = url;
  }

  getStatus(): ConnStatus {
    return this.status;
  }

  onEvent(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatus(fn: StatusListener): () => void {
    this.statusListeners.add(fn);
    // 立即推一次当前状态，避免订阅者看到 undefined
    fn(this.status);
    return () => this.statusListeners.delete(fn);
  }

  private setStatus(s: ConnStatus) {
    this.status = s;
    this.statusListeners.forEach((fn) => fn(s));
  }

  connect(): void {
    if (this.destroyed) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch (err) {
      console.error("[ws] construct failed:", err);
      this.setStatus("error");
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("connected");
      // 心跳：每 25s 发 ping
      this.pingTimer = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.send({ type: "ping" });
        }
      }, 25_000);
    };

    ws.onmessage = (ev: MessageEvent) => {
      let parsed: ServerEvent | null = null;
      try {
        parsed = JSON.parse(ev.data) as ServerEvent;
      } catch (err) {
        console.warn("[ws] bad json:", err, ev.data);
        return;
      }
      this.listeners.forEach((fn) => fn(parsed!));
    };

    ws.onerror = (ev) => {
      console.warn("[ws] error:", ev);
      this.setStatus("error");
    };

    ws.onclose = () => {
      this.cleanupSocket();
      if (this.destroyed) return;
      this.scheduleReconnect();
    };
  }

  private cleanupSocket() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    this.ws = null;
  }

  private scheduleReconnect() {
    if (this.destroyed) return;
    this.setStatus("reconnecting");
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * Math.pow(2, this.attempt));
    this.attempt += 1;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  send(msg: ClientEvent): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  /** 手动重置 attempt 并立即尝试连接（用户点重连按钮）。 */
  reconnectNow(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.attempt = 0;
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* noop */
      }
      this.cleanupSocket();
    }
    this.connect();
  }

  destroy(): void {
    this.destroyed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* noop */
      }
    }
    this.cleanupSocket();
    this.listeners.clear();
    this.statusListeners.clear();
  }
}

/** 默认 URL：浏览器同源访问 dev server 时，NEXT_PUBLIC_WS_URL=ws://localhost:8000（推荐）；或者经 Next rewrite 代理。 */
export function defaultSocketURL(sessionId: string): string {
  const fromEnv =
    typeof window !== "undefined"
      ? (window as unknown as { __WS_URL__?: string }).__WS_URL__
      : undefined;
  const base =
    fromEnv ||
    process.env.NEXT_PUBLIC_WS_URL ||
    (typeof window !== "undefined" && window.location.protocol === "https:"
      ? "wss://localhost:8000"
      : "ws://localhost:8000");
  return `${base}/ws/${sessionId}`;
}

/** HTTP API 与 WebSocket 使用同一份启动时运行配置，支持动态端口。 */
export function defaultApiBase(): string {
  const fromRuntime =
    typeof window !== "undefined"
      ? (window as unknown as { __API_BASE__?: string }).__API_BASE__
      : undefined;
  return fromRuntime || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
}

/** 把消息文本里 @<角色名> 替换成高亮 span（用于 ChatBubble 渲染）
 *  返回 React-friendly 的 token 数组：[ {type: "text", value}, {type: "mention", value, roleKey} ]
 *  调用方负责渲染。
 */
export type MentionToken =
  | { type: "text"; value: string }
  | { type: "mention"; value: string; roleKey: RoleKey };

export function parseMentions(text: string): MentionToken[] {
  if (!text) return [];
  const out: MentionToken[] = [];
  // 匹配 @角色中文名（最长 5 字）或 @role-key
  const re = /@(宋书航|药师|狂刀三浪|北河散人|白前辈|灵蝶尊者|shu-hang|yao-shi|san-lang|bei-he|bai-qianbei|ling-die)/g;
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIndex) {
      out.push({ type: "text", value: text.slice(lastIndex, m.index) });
    }
    const key = resolveRole(m[1]);
    if (key) {
      out.push({ type: "mention", value: m[1], roleKey: key });
    } else {
      out.push({ type: "text", value: m[0] });
    }
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < text.length) {
    out.push({ type: "text", value: text.slice(lastIndex) });
  }
  return out;
}
