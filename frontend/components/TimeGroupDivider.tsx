"use client";

/** TimeGroupDivider — 相邻消息间隔 5 分钟自动插入时间分组
 *  显示格式：
 *    - 今天      →  "今天 14:32"
 *    - 昨天      →  "昨天 09:15"
 *    - 同年      →  "MM-DD HH:MM"
 *    - 跨年      →  "YYYY-MM-DD HH:MM"
 *  Stage 8：深墨金主题 — 中间金字 + 左右金线分隔
 */
import { useMemo } from "react";

type Props = {
  /** ISO timestamp in ms */
  ts: number;
  /** "now" 引用（避免每次渲染都 new Date()，让 SSR/CSR 输出一致） */
  now?: number;
};

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatTimeDivider(ts: number, now: number): string {
  const d = new Date(ts);
  const today = new Date(now);
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  if (sameDay) {
    return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday =
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate();
  if (isYesterday) {
    return `昨天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  if (d.getFullYear() === today.getFullYear()) {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function TimeGroupDivider({ ts, now }: Props) {
  const label = useMemo(() => {
    const ref = now ?? Date.now();
    return formatTimeDivider(ts, ref);
  }, [ts, now]);

  return (
    <div
      className="my-4 flex w-full items-center justify-center gap-3"
      data-time-divider={ts}
      role="separator"
      aria-label={`消息分组 ${label}`}
    >
      <span className="h-px flex-1 max-w-[100px] ink-divider" />
      <span className="rounded-full border border-xz-border bg-xz-panel/70 px-3 py-1 text-[11px] font-medium tracking-wide text-xz-ink-muted shadow-sm backdrop-blur font-xiuzhen-body">
        {label}
      </span>
      <span className="h-px flex-1 max-w-[100px] ink-divider" />
    </div>
  );
}
