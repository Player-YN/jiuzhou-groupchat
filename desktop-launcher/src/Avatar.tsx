import { ROLE_META, type RoleKey } from "./roles";

type Props = {
  agentKey: RoleKey;
  emoji?: string;
  size?: "sm" | "md" | "lg";
  showRealmTag?: boolean;
  className?: string;
};

const SIZE = {
  sm: { box: "h-9 w-9", emoji: "text-xl" },
  md: { box: "h-10 w-10", emoji: "text-2xl" },
  lg: { box: "h-12 w-12", emoji: "text-3xl" },
};

/** Avatar — 启动器成员头像（深墨金主题, Stage 8） */
export default function Avatar({
  agentKey,
  emoji,
  size = "md",
  showRealmTag = false,
  className = "",
}: Props) {
  const meta = ROLE_META[agentKey];
  const s = SIZE[size];
  return (
    <div
      className={[
        "relative inline-flex shrink-0 items-center justify-center rounded-full",
        "bg-gradient-to-br shadow-md shadow-black/40",
        `ring-2 ${meta.ring}`,
        s.box,
        meta.gradient,
        className,
      ].join(" ")}
      data-agent={agentKey}
    >
      <span className={`leading-none drop-shadow-md ${s.emoji}`}>
        {emoji ?? meta.emoji}
      </span>
      {showRealmTag && (
        <span
          className={[
            "absolute -bottom-1 right-0 translate-x-1/4 translate-y-1/4",
            "rounded-full bg-ink-900 text-gold-400 font-semibold leading-none",
            "ring-1 ring-gold-500/30 shadow-sm shadow-black/50 whitespace-nowrap",
            "flex items-center justify-center h-4 px-1 text-[9px]",
          ].join(" ")}
          title={meta.realm}
        >
          {meta.realmShort}
        </span>
      )}
    </div>
  );
}
