"use client";

/** RoleProfile — 微信风格 NPC 角色资料卡 (Stage 10, 深墨金)
 *
 *  - 居中 modal/sheet：大头像 + 名字 + 境界 + 签名 (ROLE_META.blurb)
 *  - 发消息：onMessage(roleKey) → 父组件切 DM
 *  - 语音 / 视频：仅 UI stub，alert/toast「暂未开放」，无 WS/后端
 *  - 关闭：onClose
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import AgentAvatar from "./AgentAvatar";
import { ROLE_META, type RoleKey } from "@/lib/ws";
import { getCurrentStatus, getSignature } from "@/lib/rolePersona";

type Props = {
  roleKey: RoleKey;
  onClose: () => void;
  onMessage: (roleKey: RoleKey) => void;
};

export default function RoleProfile({ roleKey, onClose, onMessage }: Props) {
  const meta = ROLE_META[roleKey];
  const [toast, setToast] = useState<string | null>(null);
  const signature = useMemo(() => getSignature(roleKey), [roleKey]);
  const [statusLine, setStatusLine] = useState(() => getCurrentStatus(roleKey));

  // 打开资料时刷新状态；每小时桶变化时自动换
  useEffect(() => {
    setStatusLine(getCurrentStatus(roleKey));
    const t = setInterval(() => {
      setStatusLine(getCurrentStatus(roleKey));
    }, 60_000);
    return () => clearInterval(t);
  }, [roleKey]);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // toast 自动消失
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 1800);
    return () => clearTimeout(t);
  }, [toast]);

  const showStub = useCallback((label: string) => {
    setToast(`${label}暂未开放`);
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      data-testid="role-profile"
      data-role={roleKey}
      role="dialog"
      aria-modal="true"
      aria-label={`${meta.name} 的资料`}
    >
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-black/65 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* 卡片本体 */}
      <div
        className={[
          "relative z-10 w-full max-w-sm overflow-hidden rounded-2xl",
          "border border-[#C7A969]/30 bg-gradient-to-b from-[#2A2620] to-[#1F1F1F]",
          "shadow-2xl shadow-black/70",
          "animate-inkReveal",
        ].join(" ")}
      >
        {/* 顶部金线装饰 */}
        <div
          aria-hidden
          className="h-px w-full"
          style={{
            backgroundImage:
              "linear-gradient(90deg, transparent 0%, rgba(199, 169, 105, 0.55) 30%, rgba(212, 181, 116, 0.85) 50%, rgba(199, 169, 105, 0.55) 70%, transparent 100%)",
          }}
        />

        {/* 关闭 */}
        <button
          type="button"
          onClick={onClose}
          className="absolute right-3 top-3 z-20 rounded-md p-1.5 text-xz-ink-muted transition hover:bg-xz-panel hover:text-[#D4B574]"
          aria-label="关闭资料"
          data-testid="role-profile-close"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>

        {/* 头像区 */}
        <div className="flex flex-col items-center gap-3 px-6 pb-2 pt-8">
          <div className="relative">
            <AgentAvatar agentKey={roleKey} size="xl" ring showRealmTag />
            <span
              className="absolute bottom-1 right-1 h-3 w-3 rounded-full bg-[#5C7367] ring-2 ring-[#1F1F1F]"
              title="在线"
              aria-hidden
            />
          </div>
          <div className="text-center">
            <h2
              className={`font-xiuzhen-title text-xl font-semibold ${meta.text}`}
              data-testid="role-profile-name"
            >
              {meta.name}
            </h2>
            <div className="mt-1.5 flex items-center justify-center gap-2">
              <span className="rounded bg-xz-bg-2 px-2 py-0.5 text-[11px] font-medium text-xz-ink-muted ring-1 ring-xz-border-soft">
                {meta.realm}
              </span>
              <span className="text-[10px] font-medium text-[#7A9387]">在线</span>
            </div>
            {/* 个性签名 */}
            <p
              className="mt-3 max-w-[280px] text-sm leading-relaxed text-[#D4B574]/90 font-xiuzhen-body"
              data-testid="role-profile-signature"
            >
              「{signature || meta.blurb}」
            </p>
            {/* 当前状态（可轮换） */}
            <div
              className="mt-3 flex max-w-[280px] flex-col items-center gap-1 rounded-xl border border-[#C7A969]/20 bg-[#1A1814] px-3 py-2"
              data-testid="role-profile-status"
            >
              <span className="text-[10px] font-medium uppercase tracking-wider text-[#C7A969]/70">
                当前状态
              </span>
              <p className="text-center text-xs leading-relaxed text-xz-ink-muted font-xiuzhen-body">
                {statusLine}
              </p>
            </div>
            <p className="mt-2 text-[10px] text-xz-ink-dim" data-testid="role-profile-blurb">
              {meta.blurb}
            </p>
          </div>
        </div>

        {/* 分隔 */}
        <div className="mx-6 my-4 h-px ink-divider" aria-hidden />

        {/* 操作按钮 */}
        <div className="flex flex-col gap-2.5 px-6 pb-6">
          <button
            type="button"
            onClick={() => onMessage(roleKey)}
            data-testid="role-profile-message"
            className={[
              "flex w-full items-center justify-center gap-2 rounded-xl",
              "bg-gradient-to-r from-[#C7A969] via-[#D4B574] to-[#C7A969]",
              "px-4 py-3 text-sm font-semibold text-[#1F1F1F]",
              "shadow-md shadow-[#C7A969]/25",
              "transition hover:brightness-110 active:scale-[0.98]",
            ].join(" ")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            发消息
          </button>

          <div className="grid grid-cols-2 gap-2.5">
            <button
              type="button"
              onClick={() => showStub("语音通话")}
              data-testid="role-profile-voice"
              className={[
                "flex items-center justify-center gap-1.5 rounded-xl",
                "border border-[#C7A969]/25 bg-xz-panel/80",
                "px-3 py-2.5 text-xs font-semibold text-xz-ink",
                "transition hover:border-[#C7A969]/50 hover:bg-xz-panel-2 active:scale-[0.98]",
              ].join(" ")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />
              </svg>
              语音通话
            </button>
            <button
              type="button"
              onClick={() => showStub("视频通话")}
              data-testid="role-profile-video"
              className={[
                "flex items-center justify-center gap-1.5 rounded-xl",
                "border border-[#C7A969]/25 bg-xz-panel/80",
                "px-3 py-2.5 text-xs font-semibold text-xz-ink",
                "transition hover:border-[#C7A969]/50 hover:bg-xz-panel-2 active:scale-[0.98]",
              ].join(" ")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5" />
                <rect x="2" y="6" width="14" height="12" rx="2" />
              </svg>
              视频通话
            </button>
          </div>
        </div>

        {/* 内联 toast：暂未开放 */}
        {toast && (
          <div
            className="pointer-events-none absolute bottom-20 left-1/2 z-30 -translate-x-1/2 rounded-full border border-[#C7A969]/35 bg-[#1F1F1F]/95 px-4 py-2 text-xs font-medium text-[#D4B574] shadow-lg"
            data-testid="role-profile-toast"
            role="status"
          >
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}
