"use client";

/** AgentAvatar — 角色头像 (emoji + 角色色渐变 + 光环 + 境界小标签)
 *  Stage 8：深墨金主题适配（深背景上头像渐变更亮，境界标签用深底金字）
 */
import { ROLE_META, type RoleKey } from "@/lib/ws";

type Size = "xs" | "sm" | "md" | "lg" | "xl";

const SIZE_CLASS: Record<Size, { box: string; emoji: string; tagText: string; tagPos: string }> = {
  xs: { box: "h-6 w-6", emoji: "text-base", tagText: "text-[7px]", tagPos: "h-3 px-0.5" },
  sm: { box: "h-8 w-8", emoji: "text-lg", tagText: "text-[8px]", tagPos: "h-3.5 px-0.5" },
  md: { box: "h-10 w-10", emoji: "text-2xl", tagText: "text-[9px]", tagPos: "h-4 px-1" },
  lg: { box: "h-12 w-12", emoji: "text-3xl", tagText: "text-[10px]", tagPos: "h-4 px-1" },
  xl: { box: "h-16 w-16", emoji: "text-4xl", tagText: "text-[11px]", tagPos: "h-5 px-1.5" },
};

type Props = {
  agentKey: RoleKey | null;
  /** 自定义 emoji (server event 优先) */
  emoji?: string;
  size?: Size;
  /** 显示光环 (ring)，默认 true */
  ring?: boolean;
  /** 显示右下角境界小标签 (Stage 5-B 九洲一号群风格) */
  showRealmTag?: boolean;
  className?: string;
  /** 可选点击：头像可点时打开角色资料等 */
  onClick?: () => void;
};

export default function AgentAvatar({
  agentKey,
  emoji,
  size = "md",
  ring = true,
  showRealmTag = false,
  className = "",
  onClick,
}: Props) {
  const meta = agentKey ? ROLE_META[agentKey] : null;
  const displayEmoji = emoji ?? meta?.emoji ?? "✨";
  const gradient = meta?.gradient ?? "from-[#5C5040] to-[#3D352A]";
  const ringColor = meta?.ring ?? "ring-xz-border";
  const sizeInfo = SIZE_CLASS[size];
  const clickable = typeof onClick === "function";

  const classes = [
    "relative inline-flex shrink-0 items-center justify-center rounded-full",
    "bg-gradient-to-br shadow-md shadow-black/50",
    "select-none",
    sizeInfo.box,
    gradient,
    ring ? `ring-2 ${ringColor}` : "",
    clickable
      ? "cursor-pointer transition hover:brightness-110 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#C7A969]/60"
      : "",
    className,
  ].join(" ");

  const label = meta?.name ?? "AI";
  const body = (
    <>
      <span className={`leading-none drop-shadow-md ${sizeInfo.emoji}`}>{displayEmoji}</span>
      {showRealmTag && meta && (
        <span
          className={[
            "absolute -bottom-1 right-0 translate-x-1/4 translate-y-1/4",
            "rounded-full bg-xz-bg text-[#D4B574] font-semibold leading-none",
            "ring-1 ring-xz-border shadow-sm shadow-black/50 whitespace-nowrap",
            "flex items-center justify-center",
            sizeInfo.tagPos,
            sizeInfo.tagText,
          ].join(" ")}
          title={meta.realm}
        >
          {meta.realmShort}
        </span>
      )}
    </>
  );

  if (clickable) {
    return (
      <button
        type="button"
        className={classes}
        aria-label={`查看 ${label} 的资料`}
        data-agent={agentKey ?? "unknown"}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
      >
        {body}
      </button>
    );
  }

  return (
    <div
      className={classes}
      aria-label={label}
      data-agent={agentKey ?? "unknown"}
    >
      {body}
    </div>
  );
}
