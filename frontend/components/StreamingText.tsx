"use client";

/** StreamingText — character-level typewriter (no setInterval, just renders prop).
 *  Cursor blinks via CSS animation. Cursor color is role-themed via accentClass.
 *
 *  Stage 8：深墨金主题 — 光标金色，文字保持角色色
 *  Stage 8-B 「灵韵」: 6 NPC 各自打字节奏 — dots pulse 周期按 agentKey 不同：
 *    - shu-hang     1.6s (沉稳、慢)
 *    - yao-shi      1.2s (中)
 *    - san-lang     0.9s (急切、快)
 *    - bei-he       1.0s (中)
 *    - bai-qianbei  1.4s (慢、高冷)
 *    - ling-die     1.1s (中)
 *  通过 inline `style={{ animationDuration }}` 覆盖默认 pulseDot 时长。
 */
import { useEffect, useRef } from "react";

import type { RoleKey } from "@/lib/ws";

type Props = {
  text: string;
  /** Show blinking cursor while this is true. */
  isStreaming?: boolean;
  className?: string;
  /** Cursor + streaming text accent color (Tailwind text-* class). */
  accentClass?: string;
  /** Stage 8-B 「灵韵」: 当前 bubble 所属 NPC。决定 dots 节奏。 */
  agentKey?: RoleKey | null;
  /** Custom render function for the text portion. Default: plain text.
   *  若提供，会把 text 整个传入，由调用方决定怎么分词 / 高亮 @mention。 */
  renderText?: (text: string) => React.ReactNode;
};

/** Stage 8-B 6 NPC dots 周期 (秒) — 与 tailwind.config.js 的 dotPulse{role} 保持一致 */
const DOT_PULSE_DURATION_S: Record<RoleKey, string> = {
  "shu-hang": "1.6s",
  "yao-shi": "1.2s",
  "san-lang": "0.9s",
  "bei-he": "1.0s",
  "bai-qianbei": "1.4s",
  "ling-die": "1.1s",
};

export default function StreamingText({
  text,
  isStreaming = false,
  className = "",
  accentClass = "text-[#D4B574]",
  agentKey,
  renderText,
}: Props) {
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    // no-op
  }, [text]);

  // Stage 8-B 「灵韵」: 6 NPC dots 节奏（inline animationDuration 覆盖）
  const dotDuration =
    agentKey && DOT_PULSE_DURATION_S[agentKey]
      ? DOT_PULSE_DURATION_S[agentKey]
      : undefined;
  const dotStyle = dotDuration ? { animationDuration: dotDuration } : undefined;

  return (
    <span ref={ref} className={`whitespace-pre-wrap break-words ${className}`}>
      <span className={isStreaming ? accentClass : ""}>
        {renderText ? renderText(text) : text}
      </span>
      {isStreaming && (
        <span
          aria-hidden
          className={`ml-0.5 inline-block h-4 w-[2px] -mb-0.5 align-baseline bg-current animate-cursor ${accentClass}`}
        />
      )}
      {/* Stage 8-B: 三点 pulse — 节奏跟随 agentKey。同一 group 内 dots 错位 stagger。 */}
      {isStreaming && (
        <span
          aria-hidden
          className="ml-1 inline-flex items-center gap-[3px] align-middle"
          data-testid="npc-dot-pulse"
          data-agent={agentKey ?? "unknown"}
        >
          <span
            className="inline-block h-1 w-1 rounded-full bg-current animate-pulseDot"
            style={{ ...(dotStyle ?? {}), animationDelay: "0s" }}
          />
          <span
            className="inline-block h-1 w-1 rounded-full bg-current animate-pulseDot"
            style={{ ...(dotStyle ?? {}), animationDelay: "0.15s" }}
          />
          <span
            className="inline-block h-1 w-1 rounded-full bg-current animate-pulseDot"
            style={{ ...(dotStyle ?? {}), animationDelay: "0.3s" }}
          />
        </span>
      )}
    </span>
  );
}