"use client";

/**
 * DesktopTitleBar — Electron frameless 墨金顶栏
 * 仅在 window.jiuzhouDesktop 存在时渲染。
 * 拖拽：.desktop-titlebar-drag  |  按钮：no-drag
 */
import { useCallback, useEffect, useState } from "react";
import { getDesktopApi, isDesktopShell } from "@/lib/desktop";

export default function DesktopTitleBar() {
  const [visible, setVisible] = useState(false);
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!isDesktopShell()) return;
    setVisible(true);
    const api = getDesktopApi();
    if (!api) return;
    void api.isMaximized().then(setMaximized).catch(() => {});
    const unsub = api.onMaximizeChange?.(setMaximized);
    return () => {
      unsub?.();
    };
  }, []);

  const onMin = useCallback(() => {
    void getDesktopApi()?.minimize();
  }, []);
  const onMax = useCallback(() => {
    void getDesktopApi()
      ?.maximize()
      .then((v) => {
        if (typeof v === "boolean") setMaximized(v);
      });
  }, []);
  const onClose = useCallback(() => {
    void getDesktopApi()?.close();
  }, []);

  if (!visible) return null;

  return (
    <div
      className="desktop-titlebar-drag flex h-9 shrink-0 select-none items-center justify-between border-b border-[#C7A969]/30 bg-[#1F1F1F] px-3"
      data-testid="desktop-titlebar"
      role="banner"
      aria-label="窗口标题栏"
    >
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded bg-gradient-to-br from-[#C7A969] via-[#D4B574] to-[#8E7847] text-[10px] font-bold text-[#1F1F1F] ring-1 ring-[#D4B574]/50">
          <span className="font-xiuzhen-title leading-none">九</span>
        </div>
        <span className="font-xiuzhen-title truncate text-xs font-semibold tracking-wide text-[#E8E1D4]">
          九洲一号群
        </span>
        <span className="hidden text-[10px] text-[#C7A969]/70 sm:inline">
          · 对话式修真群
        </span>
      </div>

      <div className="desktop-titlebar-no-drag flex items-center gap-0.5">
        <button
          type="button"
          onClick={onMin}
          className="flex h-7 w-9 items-center justify-center rounded text-[#E8E1D4]/80 transition hover:bg-[#C7A969]/15 hover:text-[#E8E1D4]"
          aria-label="最小化"
          title="最小化"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
            <path d="M2 6h8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
        <button
          type="button"
          onClick={onMax}
          className="flex h-7 w-9 items-center justify-center rounded text-[#E8E1D4]/80 transition hover:bg-[#C7A969]/15 hover:text-[#E8E1D4]"
          aria-label={maximized ? "还原" : "最大化"}
          title={maximized ? "还原" : "最大化"}
        >
          {maximized ? (
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
              <path
                d="M3.5 4.5h5v5h-5zM4.5 3.5h5v1M8.5 3.5v5"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
          ) : (
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
              <rect
                x="2.5"
                y="2.5"
                width="7"
                height="7"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
          )}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex h-7 w-9 items-center justify-center rounded text-[#E8E1D4]/80 transition hover:bg-[#8B3A3A] hover:text-[#E8E1D4]"
          aria-label="关闭"
          title="关闭"
        >
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
            <path
              d="M3 3l6 6M9 3L3 9"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
