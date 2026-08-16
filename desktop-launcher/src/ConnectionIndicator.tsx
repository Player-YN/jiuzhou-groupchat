import { useEffect, useState } from "react";

type Status = "connecting" | "connected" | "disconnected";

const LABEL: Record<Status, string> = {
  connecting: "Reconnecting",
  connected: "Connected",
  disconnected: "Disconnected",
};

const COLOR: Record<Status, string> = {
  connecting: "bg-gold-500",
  connected: "bg-jade",
  disconnected: "bg-cinnabar",
};

/** ConnectionIndicator — 启动器连接状态指示（深墨金主题, Stage 8） */
export default function ConnectionIndicator() {
  const [status, setStatus] = useState<Status>("connecting");
  const [latency, setLatency] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const probe = async () => {
      const t0 = performance.now();
      try {
        const ctrl = new AbortController();
        const timeout = setTimeout(() => ctrl.abort(), 2500);
        const res = await fetch("http://localhost:3000/api/health", {
          method: "GET",
          signal: ctrl.signal,
        });
        clearTimeout(timeout);
        const dt = performance.now() - t0;
        if (!cancelled) {
          setStatus(res.ok ? "connected" : "disconnected");
          setLatency(Math.round(dt));
        }
      } catch {
        if (!cancelled) setStatus("disconnected");
      }
    };

    probe();
    timer = window.setInterval(probe, 5000);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex items-center gap-2 text-xs text-ink-text">
      <span
        className={[
          "h-2 w-2 rounded-full",
          COLOR[status],
          status !== "disconnected" ? "dot-pulse" : "",
        ].join(" ")}
      />
      <span className="font-medium font-xiuzhen-body">{LABEL[status]}</span>
      {latency !== null && status === "connected" && (
        <span className="text-ink-dim">· {latency}ms</span>
      )}
    </div>
  );
}
