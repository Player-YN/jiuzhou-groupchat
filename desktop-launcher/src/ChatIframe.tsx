import { useEffect, useRef, useState } from "react";

const FRONTEND_URL = import.meta.env.VITE_FRONTEND_URL || "http://localhost:3000";

type LoadState = "loading" | "ready" | "error";

/** ChatIframe — 启动器中间 iframe 容器（深墨金主题, Stage 8） */
export default function ChatIframe() {
  const [state, setState] = useState<LoadState>("loading");
  const [retryKey, setRetryKey] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    // 8s 兜底: 若 iframe 未触发 load 事件, 视为超时/离线
    timerRef.current = window.setTimeout(() => {
      setState((prev) => (prev === "ready" ? prev : "error"));
    }, 8000);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [retryKey]);

  return (
    <section className="flex-1 min-w-0 bg-ink-900 flex flex-col relative">
      {state !== "ready" && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-ink-900/95 text-ink-text backdrop-blur-sm">
          {state === "loading" ? (
            <>
              <div className="h-10 w-10 border-2 border-gold-500/30 border-t-gold-400 rounded-full animate-spin mb-3 shadow-lg shadow-gold-500/20" />
              <div className="text-sm font-xiuzhen-body">正在加载九洲一号群…</div>
              <div className="text-xs text-ink-muted mt-1">
                等待 {FRONTEND_URL}
              </div>
            </>
          ) : (
            <>
              <div className="text-3xl mb-2">⚠️</div>
              <div className="text-sm font-medium text-cinnabar-bright">
                无法连接九洲一号群前端
              </div>
              <div className="text-xs text-ink-muted mt-1 max-w-md text-center">
                请先启动九洲一号群 web 端 (npm run dev in{" "}
                <code className="px-1 bg-ink-700 rounded text-gold-400">frontend/</code>,
                端口 3000)
              </div>
              <button
                type="button"
                onClick={() => {
                  setState("loading");
                  setRetryKey((k) => k + 1);
                }}
                className="mt-4 px-4 py-1.5 rounded-md bg-gold-500/20 text-gold-400 hover:bg-gold-500/30 text-xs font-medium ring-1 ring-gold-500/30"
              >
                重试
              </button>
            </>
          )}
        </div>
      )}

      <iframe
        key={retryKey}
        ref={iframeRef}
        src={FRONTEND_URL}
        title="九洲一号群聊天"
        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
        allow="clipboard-read; clipboard-write"
        referrerPolicy="no-referrer-when-downgrade"
        onLoad={() => {
          if (timerRef.current) window.clearTimeout(timerRef.current);
          setState("ready");
        }}
        onError={() => setState("error")}
        className="chat-iframe w-full h-full border-0 bg-ink-900"
      />
    </section>
  );
}
