"use client";

/** ChatBubble — 单条消息气泡 (Stage 8 深墨金主题)
 *  - AI 气泡：
 *      头像 40px 在左（emoji 居中 + 境界小标签在头像右下角）
 *      角色名 + 境界在气泡上方（金色小字）
 *      气泡：深墨底 + 金色左边 4px 装饰条 + 金线边框
 *      消息文本支持 @mention 高亮（金底 + 朱砂字）
 *      时间不在气泡下展示 — 仅 TimeGroupDivider 显示时间（微信风格）
 *  - 流式中：右下角光标闪烁 + "thinking" 金色标签
 *  - User 气泡：右侧对齐，远山青主题
 *  - System 气泡：居中 pill（金线）
 *
 *  Stage 8-B 「灵韵」：
 *  - 6 NPC 各自独立入场动画（npcReveal{role}）+ 各自打字节奏（dotPulse{role}）
 *  - 回复链可视化：NPC 接另一个 NPC 的话，在气泡顶部显示 `@Xxx → 我` 提示
 */
import AgentAvatar from "./AgentAvatar";
import StreamingText from "./StreamingText";
import { ROLE_META, parseMentions, resolveRole, type RoleKey } from "@/lib/ws";

export type BubbleRole = "user" | "ai" | "system";

type Props = {
  id: string;
  role: BubbleRole;
  /** Final text. While streaming, the parent passes the partial text + isStreaming. */
  text: string;
  isStreaming?: boolean;
  /** 来自 server event 的 name (例 "宋书航") */
  speakerName?: string;
  /** 来自 server event的 emoji (例 "🌟") */
  speakerEmoji?: string;
  /** 解析后的角色 key —— 决定气泡颜色 */
  agentKey?: RoleKey | null;
  /** ISO timestamp or undefined for "just now". */
  timestamp?: number;
  /** Status like "thinking" / "done" / "error" (only shown for system). */
  status?: string;
  /** Round number (debug) */
  round?: number;
  /** 来源场景 (Bug 2 升级) — source="group" 时显示暗色徽章 "[群聊背景]" */
  source?: "group" | "dm";
  /**
   * T9 / Piece B: 人类用户的署名 (默认 "神秘人")。
   * - 群聊 + DM 都用；DM 内 author 也可能相同 (同一个 user)
   * - 缺省时显示 "我" (向后兼容)
   */
  author?: string;
  /** Stage 8-B 「灵韵」: 回复链 — 当前气泡在回复哪个 NPC（对方 RoleKey）。
   *  - backend 推送的 metadata 优先；空/null 表示降级不显示
   *  - ChatRoom MessageList 会从 messages 数组前序 AI 发言中推断
   */
  parentSpeakerKey?: RoleKey | null;
  /** Stage 8-B 「灵韵」: 气泡尺寸
   *  - "default" (default) — 群聊：px-4 py-2.5 + text-sm + max-w-[80%]
   *  - "dm" — 私信：px-5 py-4 + text-[15px] + max-w-[88%]（更亲密、间距更宽）
   */
  size?: "default" | "dm";
  /** Stage 10: AI 头像点击 → 打开角色资料（agentKey 有值时生效） */
  onAvatarClick?: (agentKey: RoleKey) => void;
};

// ===== Stage 8-B 「灵韵」helper =====

/** 入场动画：去掉会带「透明浮窗/光晕框」的 npcReveal*，改轻量无阴影淡入 */
function npcRevealClass(_agentKey?: RoleKey | null): string {
  return "animate-msgIn";
}

/** 渲染含 @mention 高亮的文本（深墨金主题） */
function renderHighlightedText(text: string) {
  const tokens = parseMentions(text);
  return tokens.map((tok, i) => {
    if (tok.type === "text") {
      return <span key={i}>{tok.value}</span>;
    }
    const meta = ROLE_META[tok.roleKey];
    return (
      <span
        key={i}
        data-mention={tok.roleKey}
        className="rounded-md bg-gradient-to-r from-[#C7A969]/25 to-[#8B3A3A]/20 px-1.5 py-0.5 font-bold ring-1 ring-[#C7A969]/35"
        style={{ color: meta?.accentHex ?? "#D4B574" }}
      >
        @{tok.value}
      </span>
    );
  });
}

/** Stage 8-B: 回复链节点。NPC 接另一个 NPC 时显示一行小字 "@Xxx → 我"。 */
function ReplyChainTag({ parentKey, myKey }: { parentKey: RoleKey; myKey: RoleKey }) {
  const parentMeta = ROLE_META[parentKey];
  const myMeta = ROLE_META[myKey];
  if (!parentMeta || !myMeta) return null;
  // 拿 parent 的 ring 颜色做左侧 1px 竖条
  return (
    <div
      className="ml-14 flex items-center gap-1.5 text-[10px] text-xz-ink-muted"
      data-testid="reply-chain-tag"
      data-parent={parentKey}
    >
      <span
        className={`h-2 w-[2px] rounded ${parentMeta.dot} opacity-70`}
        aria-hidden
      />
      <span className="font-xiuzhen-body">
        <span className={`font-semibold ${parentMeta.text}`}>@{parentMeta.name}</span>
        <span className="mx-1 text-xz-ink-dim">→</span>
        <span className={`font-semibold ${myMeta.text}`}>我</span>
      </span>
    </div>
  );
}

export default function ChatBubble({
  id,
  role,
  text,
  isStreaming = false,
  speakerName,
  speakerEmoji,
  agentKey,
  timestamp: _timestamp,
  status,
  round,
  source,
  author,
  parentSpeakerKey,
  size = "default",
  onAvatarClick,
}: Props) {
  // timestamp kept on Props for callers/TimeGroupDivider grouping; not shown under bubbles (WeChat style)
  void _timestamp;
  // Stage 8-B 「灵韵」: 群聊 vs DM 气泡尺寸差异
  const isDm = size === "dm";
  const bubblePad = isDm ? "px-5 py-4" : "px-4 py-2.5";
  const bubbleText = isDm ? "text-[15px]" : "text-sm";
  const bubbleMaxW = isDm ? "max-w-[88%]" : "max-w-[80%]";
  // System message — 金线 pill
  if (role === "system") {
    return (
      <div className="my-2 flex justify-center animate-inkReveal" data-bubble-id={id}>
        <div className="rounded-full border border-xz-border bg-xz-panel/70 px-3 py-1 text-[11px] tracking-wide text-xz-ink-muted shadow-sm backdrop-blur">
          {status || text}
        </div>
      </div>
    );
  }

  const isUser = role === "user";
  // History / partial payloads may omit agentKey — resolve from name so avatar stays clickable
  const resolvedAgentKey: RoleKey | null =
    agentKey ?? resolveRole(speakerName) ?? null;

  if (isUser) {
    // T9 / Piece B: user bubble 显示署名 (默认 "神秘人" / 来自 userIdentity).
    // 没设时显示 "我" (向后兼容 — 旧 row 没有 author 字段).
    const authorLabel = author || "我";
    return (
      <div
        className="flex w-full flex-col items-end gap-1 animate-inkReveal"
        data-bubble-id={id}
        data-role="user"
        data-author={authorLabel}
      >
        <div className="flex items-end gap-2">
          <div
            className={[
              "relative rounded-2xl border border-[#C7A969]/25 bg-[#2A2620] leading-relaxed text-[#E8E1D4]",
              bubbleMaxW,
              bubblePad,
              bubbleText,
            ].join(" ")}
          >
            {/* 左边 4px 远山青装饰条 */}
            <span
              className="absolute left-0 top-0 h-full w-1 rounded-l-2xl bg-gradient-to-b from-[#5C7367] to-[#7A9387]"
              aria-hidden
            />
            <div className="mb-0.5 pl-1 text-[11px] font-semibold text-[#7A9387]" data-testid="bubble-author">
              我：{authorLabel}
            </div>
            <div className="pl-1">{renderHighlightedText(text)}</div>
          </div>
          <div
            className={[
              "flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#5C7367] to-[#7A9387] font-bold text-xz-bg ring-2 ring-[#5C7367]/50 shadow-md shadow-black/40",
              isDm ? "h-12 w-12 text-lg" : "h-10 w-10 text-base",
            ].join(" ")}
          >
            我
          </div>
        </div>
      </div>
    );
  }

  // AI message — 九洲一号群「深墨金」风格 + Stage 8-B 各自入场动画
  const meta = resolvedAgentKey ? ROLE_META[resolvedAgentKey] : null;
  const accentGradient = meta?.accent ?? "from-[#C7A969] to-[#D4B574]";
  const nameColor = meta?.text ?? "text-[#D4B574]";
  const isGroupSource = source === "group";
  const revealClass = npcRevealClass(resolvedAgentKey);

  return (
    <div
      className={`flex w-full flex-col items-start gap-1.5 ${revealClass}`}
      data-bubble-id={id}
      data-role="ai"
      data-agent={resolvedAgentKey ?? "unknown"}
      data-source={source ?? "dm"}
    >
      {/* 角色名 + 境界 — 金色小字位于气泡上方 */}
      <div className="ml-14 flex items-center gap-1.5 text-[11px] text-xz-ink-muted">
        <span className={`font-semibold ${nameColor} font-xiuzhen-body`}>
          {speakerName || meta?.name || "AI"}
        </span>
        {/* Bug 1 + Bug 2 升级: 来源场景徽章 — 朱砂底金字 */}
        {isGroupSource && (
          <span
            className="rounded-full bg-[#8B3A3A] px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-xz-ink ring-1 ring-[#A84545]/60"
            data-testid="source-group-badge"
            title="这条消息来自群聊 (per-agent unified memory fan-out)"
          >
            群聊背景
          </span>
        )}
        {meta && (
          <span className="rounded bg-xz-bg-2 px-1.5 py-px text-[10px] font-medium text-xz-ink-muted ring-1 ring-xz-border-soft">
            {meta.realm}
          </span>
        )}
        {typeof round === "number" && (
          <span className="rounded-full bg-xz-bg-2 px-1.5 py-px text-[9px] font-medium text-xz-ink-dim ring-1 ring-xz-border-soft">
            R{round}
          </span>
        )}
        {isStreaming && (
          <span className="flex items-center gap-0.5 text-[9px] font-normal text-[#D4B574]">
            <span className="h-1 w-1 animate-pulse rounded-full bg-[#C7A969]" />
            <span>streaming</span>
          </span>
        )}
      </div>

      {/* Stage 8-B: 回复链 — 仅 NPC → NPC 显示，user 永不显示 */}
      {parentSpeakerKey &&
        resolvedAgentKey &&
        parentSpeakerKey !== resolvedAgentKey && (
        <ReplyChainTag parentKey={parentSpeakerKey} myKey={resolvedAgentKey} />
      )}

      <div className="flex items-end gap-2">
        {/* 头像 — DM 模式用 lg (h-12 w-12) 更亲密，群聊 md；可点开资料 */}
        <AgentAvatar
          agentKey={resolvedAgentKey}
          emoji={speakerEmoji}
          size={isDm ? "lg" : "md"}
          ring
          showRealmTag
          onClick={
            resolvedAgentKey && onAvatarClick
              ? () => onAvatarClick(resolvedAgentKey)
              : undefined
          }
        />

        {/* 气泡 — 不透明深墨面板（取消玻璃/半透明浮窗感） */}
        <div
          className={[
            "group relative overflow-hidden rounded-xl leading-relaxed",
            "border border-[#C7A969]/25 bg-[#2A2620] text-[#E8E1D4]",
            bubbleMaxW,
            bubblePad,
            bubbleText,
          ].join(" ")}
        >
          {/* 左边 4px 角色色装饰条 */}
          <span
            className={`absolute left-0 top-0 h-full w-1 bg-gradient-to-b ${accentGradient}`}
            aria-hidden
          />
          <div>
            {isStreaming ? (
              <StreamingText
                text={text}
                isStreaming={isStreaming}
                accentClass={nameColor}
                agentKey={resolvedAgentKey}
                renderText={(seg) => renderHighlightedText(seg)}
              />
            ) : (
              renderHighlightedText(text)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}