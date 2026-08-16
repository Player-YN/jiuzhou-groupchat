import { useEffect, useState } from "react";

type SplashProps = {
  onDone: () => void;
  /** 最小显示时长 (ms), 避免启动器白闪 */
  minDurationMs?: number;
};

/** Splash — 桌面启动器开场动画 (Stage 8 九洲一号群「深墨金」主题) */
export default function Splash({ onDone, minDurationMs = 1100 }: SplashProps) {
  const [phase, setPhase] = useState<"show" | "fade">("show");
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const t0 = performance.now();
    let raf = 0;
    let elapsed = 0;

    const tick = () => {
      elapsed = performance.now() - t0;
      const p = Math.min(100, (elapsed / minDurationMs) * 100);
      setProgress(p);
      if (p < 100) {
        raf = window.setTimeout(tick, 60) as unknown as number;
      } else {
        setPhase("fade");
        window.setTimeout(onDone, 550);
      }
    };
    tick();
    return () => {
      if (raf) window.clearTimeout(raf);
    };
  }, [minDurationMs, onDone]);

  return (
    <div
      className={[
        "fixed inset-0 z-50 flex flex-col items-center justify-center",
        "bg-gradient-to-br from-ink-900 via-ink-800 to-ink-700",
        phase === "fade" ? "splash-fade-out" : "",
      ].join(" ")}
    >
      {/* Logo — 金色装饰 + 仙侠书法 */}
      <div className="relative mb-6">
        <div className="absolute inset-0 rounded-full bg-gold-500/20 blur-2xl animate-pulse-slow" />
        <div className="relative h-20 w-20 rounded-2xl bg-gradient-to-br from-gold-400 via-gold-500 to-gold-600 flex items-center justify-center shadow-2xl shadow-gold-500/40 ring-1 ring-gold-400/40">
          <span className="font-xiuzhen-title text-3xl text-ink-900">九</span>
        </div>
      </div>

      {/* 群名 — 金光流文字 */}
      <h1 className="text-3xl font-bold gold-text tracking-wider mb-1 font-xiuzhen-title">
        九洲一号群
      </h1>
      <p className="text-sm text-ink-muted mb-8">九洲一号群聊天群 · 启动中</p>

      {/* 进度条 — 金色流光 */}
      <div className="w-56 h-1.5 bg-ink-600 rounded-full overflow-hidden ring-1 ring-gold-500/20">
        <div
          className="h-full bg-gradient-to-r from-gold-500 via-gold-400 to-gold-300 transition-all duration-100"
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="mt-3 text-[11px] text-ink-muted">
        Loading… {Math.floor(progress)}%
      </div>

      {/* 版本 */}
      <div className="absolute bottom-4 text-[10px] text-ink-dim">
        v0.1.0 · Tauri 2 desktop launcher · 深墨金 v1
      </div>
    </div>
  );
}
