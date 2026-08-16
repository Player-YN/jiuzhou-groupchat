"use client";

/** Connection status pill — 深墨金主题 (Stage 8). */
import type { ConnStatus } from "@/lib/ws";

type Props = {
  status: ConnStatus;
  onReconnect?: () => void;
  /** 角色数量（默认 6 九洲一号群） */
  agentCount?: number;
};

const META: Record<ConnStatus, { dot: string; label: string; ring: string }> = {
  connected: {
    dot: "bg-[#5C7367]",
    ring: "ring-[#5C7367]/30",
    label: "Connected",
  },
  connecting: {
    dot: "bg-[#C7A969] animate-pulseDot",
    ring: "ring-[#C7A969]/30",
    label: "Connecting…",
  },
  reconnecting: {
    dot: "bg-[#C7A969] animate-pulseDot",
    ring: "ring-[#C7A969]/30",
    label: "Reconnecting…",
  },
  disconnected: {
    dot: "bg-xz-ink-dim",
    ring: "ring-xz-ink-dim/30",
    label: "Disconnected",
  },
  error: {
    dot: "bg-[#8B3A3A]",
    ring: "ring-[#8B3A3A]/30",
    label: "Error",
  },
};

export default function ConnectionStatus({ status, onReconnect, agentCount = 6 }: Props) {
  const m = META[status];
  const isOffline = status === "disconnected" || status === "error";
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-xz-border bg-xz-panel/70 px-3 py-1.5 text-xs font-medium text-xz-ink-muted shadow-sm backdrop-blur">
      <span className={`h-2 w-2 rounded-full ${m.dot} ring-4 ${m.ring}`} aria-hidden />
      <span>{m.label}</span>
      {status === "connected" && (
        <span className="text-[10px] text-xz-ink-dim">· {agentCount} 友</span>
      )}
      {isOffline && onReconnect && (
        <button
          type="button"
          onClick={onReconnect}
          className="ml-1 rounded-full bg-[#8B3A3A]/30 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#A84545] transition hover:bg-[#8B3A3A]/50"
        >
          Reconnect
        </button>
      )}
    </div>
  );
}
