"use client";

/** User identity — T9 / Piece B "神秘人" 默认 user ID + 可改.
 *
 *  The frontend stores the human user's display name in localStorage so it
 *  survives page refresh / new tab / browser restart (mirrors how
 *  ChatContext stores `chat-session-id` for the group session).
 *
 *  Default value is "神秘人" (anonymous cultivator) — a WeChat-style fallback
 *  that's neutral, gender-agnostic, and on-theme with the 修真群 world.
 *  The user can change it via the inline ✎ editor in the chat header
 *  (ChatRoom) or DM header (DMWindow).
 *
 *  Privacy: stays in the user's browser only. The name is sent to the
 *  backend as the `author` field of `user_msg` and `dm_msg` payloads;
 *  the backend persists it on AgentMemoryEntry rows.
 */

const STORAGE_KEY = "user-display-name";
/** Default fallback when no value is set or the stored value is whitespace-only. */
export const DEFAULT_DISPLAY_NAME = "神秘人";
/** Hard cap to keep UI tidy + prevent accidental 5MB paste. */
const MAX_NAME_LENGTH = 24;

/** Read the persisted display name. Returns DEFAULT_DISPLAY_NAME when no
 *  value exists, the stored value is malformed, or localStorage is blocked
 *  (privacy mode / SSR). Never throws.
 */
export function getDisplayName(): string {
  if (typeof window === "undefined") return DEFAULT_DISPLAY_NAME;
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (typeof v === "string" && v.trim().length > 0 && v.length <= MAX_NAME_LENGTH) {
      return v;
    }
  } catch {
    // localStorage blocked / quota — fall through to default
  }
  return DEFAULT_DISPLAY_NAME;
}

/** Persist a new display name. Trims whitespace and clamps to MAX_NAME_LENGTH;
 *  falls back to DEFAULT_DISPLAY_NAME if the trimmed string is empty.
 *  Returns the effective value that was actually written.
 *
 *  Silently no-ops on SSR / localStorage-blocked environments.
 */
export function setDisplayName(name: string): string {
  if (typeof window === "undefined") return DEFAULT_DISPLAY_NAME;
  const trimmed = (name ?? "").trim().slice(0, MAX_NAME_LENGTH);
  const effective = trimmed.length > 0 ? trimmed : DEFAULT_DISPLAY_NAME;
  try {
    window.localStorage.setItem(STORAGE_KEY, effective);
  } catch {
    // ignore quota / privacy errors
  }
  return effective;
}

/** Storage event key — for cross-tab sync, we listen on this and re-read. */
function storageEventKey(): string {
  return STORAGE_KEY;
}

/** React hook that exposes the persisted display name as live state and
 *  syncs across tabs via the `storage` event (Chrome / Firefox broadcast
 *  localStorage changes across windows of the same origin automatically).
 *
 *  Usage:
 *    const { displayName, setDisplayName } = useUserIdentity();
 *
 *  Re-renders trigger:
 *    1. setDisplayName(newName) called in this tab.
 *    2. Another tab updates the same key (storage event).
 */
import { useCallback, useEffect, useState } from "react";

export function useUserIdentity(): {
  displayName: string;
  setDisplayName: (name: string) => void;
} {
  const [displayName, setLocal] = useState<string>(DEFAULT_DISPLAY_NAME);

  // Read on mount + listen for cross-tab updates
  useEffect(() => {
    setLocal(getDisplayName());
    const onStorage = (e: StorageEvent) => {
      if (e.key === storageEventKey()) {
        setLocal(getDisplayName());
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setDisplayName = useCallback((name: string) => {
    const effective = setDisplayNameImpl(name);
    setLocal(effective);
    // Also dispatch a storage event so other hooks in this tab update
    // (the native 'storage' event only fires across tabs, not within one).
    if (typeof window !== "undefined") {
      try {
        window.dispatchEvent(
          new StorageEvent("storage", {
            key: STORAGE_KEY,
            newValue: effective,
          }),
        );
      } catch {
        // Some browsers refuse manual StorageEvent — fall through, it's not fatal.
      }
    }
  }, []);

  return { displayName, setDisplayName };
}

// Internal: same as the free function but imported only inside the hook body
// to avoid a name shadowing issue.
function setDisplayNameImpl(name: string): string {
  if (typeof window === "undefined") return DEFAULT_DISPLAY_NAME;
  const trimmed = (name ?? "").trim().slice(0, MAX_NAME_LENGTH);
  const effective = trimmed.length > 0 ? trimmed : DEFAULT_DISPLAY_NAME;
  try {
    window.localStorage.setItem(STORAGE_KEY, effective);
  } catch {
    // ignore
  }
  return effective;
}