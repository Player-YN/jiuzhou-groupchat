"use client";

/** ChatInput — textarea + send button (深墨金主题).
 *  Submit on Enter (Shift+Enter for newline). */
import { useCallback, useRef } from "react";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled?: boolean;
  placeholder?: string;
};

export default function ChatInput({
  value,
  onChange,
  onSend,
  disabled = false,
  placeholder = "发个消息…",
}: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend();
    // Reset textarea height
    if (ref.current) {
      ref.current.style.height = "auto";
    }
  }, [value, disabled, onSend]);

  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
    },
    [submit]
  );

  const handleInput = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
      // Auto-resize, hard-cap height so composer cannot crush the stage
      const ta = e.target;
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 4 * 24)}px`;
    },
    [onChange]
  );

  return (
    <div className="flex w-full items-end gap-2 rounded-2xl border border-xz-border bg-xz-panel p-2 shadow-lg shadow-black/30 transition focus-within:border-[#C7A969]/60 focus-within:shadow-[0_0_0_3px_rgba(199,169,105,0.12)]">
      <textarea
        ref={ref}
        value={value}
        onChange={handleInput}
        onKeyDown={handleKey}
        rows={1}
        disabled={disabled}
        placeholder={placeholder}
        className="min-h-[40px] max-h-24 flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2 text-sm text-xz-ink placeholder-xz-ink-dim outline-none focus:outline-none disabled:opacity-50 font-xiuzhen-body"
        data-testid="chat-input"
      />
      <button
        type="button"
        onClick={submit}
        disabled={disabled || !value.trim()}
        className="flex h-10 shrink-0 items-center gap-1.5 rounded-xl bg-gradient-to-br from-[#C7A969] to-[#D4B574] px-4 text-sm font-semibold text-xz-bg shadow-md shadow-[#C7A969]/30 transition hover:from-[#D4B574] hover:to-[#E0C58A] disabled:cursor-not-allowed disabled:opacity-40"
        data-testid="send-button"
      >
        发送
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m22 2-7 20-4-9-9-4Z" />
          <path d="M22 2 11 13" />
        </svg>
      </button>
    </div>
  );
}
